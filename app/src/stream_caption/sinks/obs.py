"""OBS へ字幕を送る (obs-websocket v5 の SendStreamCaption)。

OBS は受け取った文字を CEA-608 字幕として配信映像のデータに乗せる。
YouTube / Twitch のプレイヤーの CC ボタンで表示される(焼き付けではない)。
CEA-608 は日本語の文字を送れないため、英語以外は送らない。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import logging

import websockets

from ..captions import has_non_latin, split_caption, to_cea608_text
from ..config import CaptionConfig, ObsSinkConfig
from .base import Caption, QueuedSink

log = logging.getLogger(__name__)

OP_HELLO, OP_IDENTIFY, OP_IDENTIFIED, OP_REQUEST, OP_REQUEST_RESPONSE = 0, 1, 2, 6, 7
CODE_OUTPUT_NOT_RUNNING = 501


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsRequestError(RuntimeError):
    def __init__(self, request_type: str, code: int, comment: str | None):
        super().__init__(f"{request_type} failed: code={code} {comment or ''}".strip())
        self.code = code


class ObsWebSocketClient:
    """必要最小限の obs-websocket v5 クライアント。切断されたら自動で再接続する。"""

    def __init__(self, host: str, port: int, password: str = "", reconnect_seconds: float = 5.0):
        self.url = f"ws://{host}:{port}"
        self.password = password
        self.reconnect_seconds = reconnect_seconds
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._connected = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None
        self.obs_version: str | None = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="obs-websocket")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def wait_connected(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except TimeoutError:
            return False

    async def _run(self) -> None:
        while True:
            try:
                async with websockets.connect(self.url, max_size=2**22, open_timeout=5) as ws:
                    await self._identify(ws)
                    self._ws = ws
                    self._connected.set()
                    self.last_error = None
                    log.info("OBS に接続しました (%s, OBS %s)", self.url, self.obs_version)
                    async for raw in ws:
                        self._dispatch(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                if message != self.last_error:
                    log.warning("OBS に接続できません (%s): %s", self.url, message)
                self.last_error = message
            finally:
                self._ws = None
                self._connected.clear()
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("OBS との接続が切れました"))
                self._pending.clear()
            await asyncio.sleep(self.reconnect_seconds)

    async def _identify(self, ws) -> None:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if hello.get("op") != OP_HELLO:
            raise ConnectionError(f"unexpected first message: {hello}")
        data = hello.get("d", {})
        self.obs_version = data.get("obsWebSocketVersion")
        identify = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = data.get("authentication")
        if auth:
            if not self.password:
                raise PermissionError("OBS 側でパスワードが設定されていますが、OBS_PASSWORD が空です")
            identify["authentication"] = auth_string(self.password, auth["salt"], auth["challenge"])
        await ws.send(json.dumps({"op": OP_IDENTIFY, "d": identify}))
        reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if reply.get("op") != OP_IDENTIFIED:
            raise PermissionError(f"OBS の認証に失敗しました: {reply}")

    def _dispatch(self, message: dict) -> None:
        if message.get("op") != OP_REQUEST_RESPONSE:
            return  # イベントは購読していない
        data = message.get("d", {})
        future = self._pending.pop(data.get("requestId"), None)
        if future and not future.done():
            future.set_result(data)

    async def request(
        self, request_type: str, request_data: dict | None = None, timeout: float = 5.0
    ) -> dict:
        ws = self._ws
        if ws is None:
            raise ConnectionError("OBS に接続していません")
        request_id = str(next(self._ids))
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {"requestType": request_type, "requestId": request_id}
        if request_data is not None:
            payload["requestData"] = request_data
        await ws.send(json.dumps({"op": OP_REQUEST, "d": payload}))
        try:
            data = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)
        status = data.get("requestStatus", {})
        if not status.get("result"):
            raise ObsRequestError(request_type, status.get("code", -1), status.get("comment"))
        return data.get("responseData") or {}


class ObsCaptionSink(QueuedSink):
    name = "obs"

    def __init__(
        self,
        config: ObsSinkConfig,
        captions: CaptionConfig,
        client: ObsWebSocketClient | None = None,
    ):
        super().__init__(enabled=config.enabled)
        self.captions = captions
        self.client = client or ObsWebSocketClient(
            config.host, config.port, config.password, config.reconnect_seconds
        )
        self.streaming_warning = False

    async def start(self) -> None:
        if self.enabled:
            await self.client.start()
        await super().start()

    async def stop(self) -> None:
        await super().stop()
        await self.client.stop()

    async def send(self, caption: Caption) -> None:
        # CEA-608 は日本語を送れないので、ラテン文字の行(英語)だけを送る。
        # 「英語＋日本語」モードでは英語だけが OBS(Twitch など)に出る。
        latin = " ".join(text for _, text in caption.lines if not has_non_latin(text))
        if not latin:
            self.skipped += 1
            self.last_error = "日本語は CEA-608 で送れないため、OBS には送信していません"
            return
        text = to_cea608_text(latin)
        if not text:
            self.skipped += 1
            return
        if not self.client.connected:
            self.skipped += 1
            self.last_error = self.client.last_error or "OBS に接続していません"
            return
        chunks = split_caption(text, self.captions.max_chars)
        for index, chunk in enumerate(chunks):
            try:
                await self.client.request("SendStreamCaption", {"captionText": chunk})
            except ObsRequestError as exc:
                if exc.code == CODE_OUTPUT_NOT_RUNNING:
                    # 配信していないときは送れない(エラーではないので静かに数えるだけ)
                    self.skipped += 1
                    self.streaming_warning = True
                    self.last_error = "OBS が配信中ではないため、字幕は送られていません"
                    return
                raise
            self.streaming_warning = False
            self.last_error = None
            self.sent += 1
            if index + 1 < len(chunks):
                # 次の塊を送る前に、読める程度の時間を空ける
                await asyncio.sleep(len(chunk) / max(1.0, self.captions.chars_per_second))

    def status(self) -> dict:
        status = super().status()
        status.update(
            connected=self.client.connected,
            url=self.client.url,
            obs_version=self.client.obs_version,
        )
        return status
