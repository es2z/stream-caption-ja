"""マイク音声 -> 文の区切り -> 日本語認識 -> (英語翻訳) -> 字幕の送信、をつなぐ。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass

import numpy as np

from .asr import Recognizer
from .config import MODES, AppConfig
from .segmenter import SAMPLE_RATE, Phrase, PhraseSegmenter
from .sinks import Caption, QueuedSink
from .translator import Translator

log = logging.getLogger(__name__)

_PUNCT = re.compile(r"[\s、。,.!?！？…・ー〜~]+")


@dataclass
class CaptionEvent:
    """画面表示用の記録。"""

    id: int
    source: str
    text: str
    lang: str
    spoken_at: float
    audio_seconds: float
    asr_ms: int
    translate_ms: int
    total_ms: int  # 話し終わり(文の確定)から送信開始まで
    note: str = ""


class Pipeline:
    def __init__(
        self,
        config: AppConfig,
        recognizer: Recognizer,
        translator: Translator | None,
        sinks: list[QueuedSink],
    ):
        self.config = config
        self.mode = config.mode
        self.recognizer = recognizer
        self.translator = translator
        self.sinks = {sink.name: sink for sink in sinks}
        self._phrases: asyncio.Queue[tuple[Phrase, float, float]] = asyncio.Queue(maxsize=10)
        self._listeners: set[asyncio.Queue] = set()
        self._worker: asyncio.Task | None = None
        self._ids = 0
        self.history: list[CaptionEvent] = []
        self.dropped_phrases = 0

    # -- 起動・停止 -------------------------------------------------------------

    async def start(self) -> None:
        for sink in self.sinks.values():
            await sink.start()
        self._worker = asyncio.create_task(self._work(), name="pipeline")

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        for sink in self.sinks.values():
            await sink.stop()
        if self.translator:
            await self.translator.aclose()
        self.recognizer.close()

    # -- 設定 -------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode != self.mode:
            self.mode = mode
            if self.translator:
                self.translator.reset_history()
            log.info("字幕モードを %s に変更しました", mode)

    # -- 画面への通知 -----------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    def _notify(self, message: dict) -> None:
        for queue in list(self._listeners):
            if queue.full():
                continue
            queue.put_nowait(message)

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "asr": self.recognizer.name,
            "translator": bool(self.translator),
            "dropped_phrases": self.dropped_phrases,
            "silence_dbfs": self.config.segmenter.silence_dbfs,
            "sinks": {name: sink.status() for name, sink in self.sinks.items()},
        }

    # -- 音声の入口 -------------------------------------------------------------

    def new_session(self) -> AudioSession:
        return AudioSession(self)

    def enqueue(self, phrase: Phrase, spoken_at: float, settled_at: float) -> None:
        if self._phrases.full():
            # 認識が追いつかないときは古い文を捨てて遅延が膨らむのを防ぐ
            self._phrases.get_nowait()
            self.dropped_phrases += 1
            log.warning("処理が追いつかないため、古い文を1つ捨てました")
        self._phrases.put_nowait((phrase, spoken_at, settled_at))

    # -- 本体 -------------------------------------------------------------------

    def _should_drop(self, text: str) -> bool:
        stripped = _PUNCT.sub("", text)
        if len(stripped) < self.config.filters.min_chars:
            return True
        drop = {_PUNCT.sub("", t) for t in self.config.filters.drop_texts}
        return stripped in drop

    async def _work(self) -> None:
        while True:
            phrase, spoken_at, settled_at = await self._phrases.get()
            try:
                await self._process(phrase, spoken_at, settled_at)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("文の処理中にエラーが発生しました")

    async def _process(self, phrase: Phrase, spoken_at: float, settled_at: float) -> None:
        t0 = time.perf_counter()
        source = (await self.recognizer.transcribe(phrase.audio)).strip()
        t1 = time.perf_counter()
        if not source or self._should_drop(source):
            return
        mode, text, note = self.mode, source, ""
        translate_ms = 0
        if mode == "en":
            if self.translator is None:
                note = "翻訳が無効なため日本語のまま"
                mode = "ja"
            else:
                try:
                    text = await self.translator.translate(source)
                except Exception as exc:
                    log.warning("翻訳に失敗しました: %s", exc)
                    self._notify({"type": "error", "message": f"翻訳に失敗しました: {exc}"})
                    return
                translate_ms = int((time.perf_counter() - t1) * 1000)
                if not text:
                    return
        caption = Caption(text=text, source=source, lang=mode, spoken_at=spoken_at)
        for sink in self.sinks.values():
            sink.submit(caption)
        self._ids += 1
        event = CaptionEvent(
            id=self._ids,
            source=source,
            text=text,
            lang=mode,
            spoken_at=spoken_at,
            audio_seconds=round(phrase.seconds, 2),
            asr_ms=int((t1 - t0) * 1000),
            translate_ms=translate_ms,
            total_ms=int((time.time() - settled_at) * 1000),
            note=note,
        )
        self.history = (self.history + [event])[-50:]
        log.info("[%s] %s => %s (%dms)", mode, source, text, event.total_ms)
        self._notify({"type": "caption", "caption": asdict(event)})


class AudioSession:
    """ブラウザ1つ分の音声入力。受け取った PCM を区切って Pipeline に渡す。"""

    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline
        self.segmenter = PhraseSegmenter(pipeline.config.segmenter)
        self._last_frame_wall = time.time()

    def _wall_time_of(self, sample: int) -> float:
        """セッション内のサンプル位置を実時刻に直す(最新フレームの受信時刻から逆算)。"""
        behind = (self.segmenter.received_samples - sample) / SAMPLE_RATE
        return self._last_frame_wall - behind

    def push_pcm16(self, payload: bytes) -> float:
        """16kHz モノラル 16bit リトルエンディアン PCM を受け取る。戻り値は音量(dBFS)。"""
        payload = payload[: len(payload) // 2 * 2]
        if not payload:
            return -120.0
        frame = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
        phrases = self.segmenter.push(frame)
        self._last_frame_wall = time.time()
        for phrase in phrases:
            self.pipeline.enqueue(phrase, self._wall_time_of(phrase.start_sample), time.time())
        rms = self.segmenter.last_rms
        return float(20 * np.log10(rms)) if rms > 1e-6 else -120.0

    def close(self) -> None:
        for phrase in self.segmenter.flush():
            self.pipeline.enqueue(phrase, self._wall_time_of(phrase.start_sample), time.time())
