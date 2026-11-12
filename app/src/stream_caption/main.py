"""Web サーバー。ブラウザからマイク音声を受け取り、字幕を OBS / YouTube へ送る。

    python -m stream_caption.main            # config.yaml と環境変数を読んで起動

ブラウザで http://localhost:7860 を開き、マイクを選んで「開始」を押す。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .asr import create_recognizer
from .captions import has_non_latin
from .config import AppConfig, load_config
from .pipeline import Pipeline
from .sinks import Caption, ObsCaptionSink, YouTubeCaptionSink
from .translator import PlamoTranslator

log = logging.getLogger("stream_caption")
STATIC = Path(__file__).parent / "static"


class SettingsUpdate(BaseModel):
    mode: str | None = None  # en / ja / both
    bilingual_order: str | None = None  # ja_first / en_first
    obs_enabled: bool | None = None
    youtube_enabled: bool | None = None
    youtube_url: str | None = None  # 画面から貼り付ける場合(保存はせずメモリ上のみ)


class TestCaption(BaseModel):
    text: str


async def build_pipeline(config: AppConfig) -> Pipeline:
    # モデルの読み込みは重いので別スレッドで行う
    recognizer = await asyncio.to_thread(create_recognizer, config.asr)
    translator = PlamoTranslator(config.translator)
    sinks = [
        ObsCaptionSink(config.sinks.obs, config.captions),
        YouTubeCaptionSink(config.sinks.youtube, config.captions),
    ]
    pipeline = Pipeline(config, recognizer, translator, sinks)
    await pipeline.start()
    return pipeline


def create_app(config: AppConfig, pipeline_factory=build_pipeline) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pipeline = await pipeline_factory(config)
        youtube = app.state.pipeline.sinks.get("youtube")
        if youtube and youtube.enabled and youtube.ingestion_url:
            with contextlib.suppress(Exception):
                await youtube.heartbeat()
        log.info("準備ができました: http://localhost:7860")
        yield
        await app.state.pipeline.stop()

    app = FastAPI(title="stream-caption-ja", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # --- (任意) 画面へのアクセス制限 ----------------------------------------
    # 既定ではポートを 127.0.0.1 にしか公開しないので不要。LAN 内の別 PC から
    # 操作したいなどでポートを外に公開する場合は、下を有効にして UI_TOKEN を設定し、
    # http://<ホスト>:7860/?token=... で開く(WebSocket にも同じ確認を足すこと)。
    #
    # from fastapi import Request
    # from fastapi.responses import PlainTextResponse
    #
    # @app.middleware("http")
    # async def require_token(request: Request, call_next):
    #     token = os.environ.get("UI_TOKEN")
    #     if token and request.query_params.get("token") != token \
    #             and request.headers.get("x-ui-token") != token:
    #         return PlainTextResponse("forbidden", status_code=403)
    #     return await call_next(request)
    # -------------------------------------------------------------------------

    def pipeline() -> Pipeline:
        return app.state.pipeline

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/status")
    async def status() -> dict:
        p = pipeline()
        result = p.status()
        if isinstance(p.translator, PlamoTranslator):
            result["translator_ready"] = await p.translator.ready()
        return result

    @app.post("/api/settings")
    async def update_settings(update: SettingsUpdate) -> dict:
        p = pipeline()
        if update.mode is not None:
            try:
                p.set_mode(update.mode)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if update.bilingual_order is not None:
            try:
                p.set_bilingual_order(update.bilingual_order)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if update.obs_enabled is not None and "obs" in p.sinks:
            sink = p.sinks["obs"]
            sink.enabled = update.obs_enabled
            if sink.enabled:
                await sink.client.start()
        youtube = p.sinks.get("youtube")
        if youtube is not None:
            if update.youtube_url is not None:
                youtube.set_url(update.youtube_url.strip())
            if update.youtube_enabled is not None:
                youtube.enabled = update.youtube_enabled
            if (update.youtube_url or update.youtube_enabled) and youtube.ingestion_url:
                try:
                    await youtube.heartbeat()
                except Exception as exc:
                    youtube.last_error = f"疎通確認に失敗しました: {exc}"
        return p.status()

    @app.post("/api/test-caption")
    async def test_caption(body: TestCaption) -> dict:
        """しゃべらずに送信経路を確認する(入力した文字をそのまま字幕として送る)。"""
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "text is empty")
        p = pipeline()
        lang = "ja" if has_non_latin(text) else "en"
        caption = Caption(lines=((lang, text),), source=text, spoken_at=time.time())
        for sink in p.sinks.values():
            sink.submit(caption)
        return {"ok": True}

    @app.websocket("/ws/audio")
    async def audio_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        session = pipeline().new_session()
        last_level = 0.0
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data:
                    dbfs = session.push_pcm16(data)
                    now = time.monotonic()
                    if now - last_level > 0.1:
                        last_level = now
                        await websocket.send_text(json.dumps({"type": "level", "dbfs": round(dbfs, 1)}))
        except WebSocketDisconnect:
            pass
        finally:
            session.close()

    @app.websocket("/ws/events")
    async def events_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        p = pipeline()
        queue = p.subscribe()

        async def wait_disconnect() -> None:
            # こちらからは送るだけだが、受信していないと切断に気付けないので読み続ける
            while (await websocket.receive()).get("type") != "websocket.disconnect":
                pass

        disconnected = asyncio.create_task(wait_disconnect())
        try:
            await websocket.send_json({"type": "history", "captions": [asdict(e) for e in p.history]})
            while not disconnected.done():
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {getter, disconnected}, timeout=1.0, return_when="FIRST_COMPLETED"
                )
                if getter in done:
                    await websocket.send_json(getter.result())
                else:
                    getter.cancel()
                    if not disconnected.done():
                        await websocket.send_json({"type": "status", "status": p.status()})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            disconnected.cancel()
            p.unsubscribe(queue)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="stream-caption-ja server")
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", "/config/config.yaml"))
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7860")))
    args = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    app = create_app(config)
    # Haas のデモと同じく、純 Python の h11 / wsproto バックエンドを使う
    uvicorn.run(app, host=args.host, port=args.port, http="h11", ws="wsproto", log_level="warning")


if __name__ == "__main__":
    main()
