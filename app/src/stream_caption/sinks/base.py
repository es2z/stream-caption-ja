"""字幕の送り先の共通部分。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Caption:
    """送る字幕1件。lines は表示する順に (言語, 文) を並べたもの。

    - 英語モード:   (("en", 訳文),)
    - 日本語モード: (("ja", 認識結果),)
    - 両方モード:   (("ja", 認識結果), ("en", 訳文)) または英語が先
    """

    lines: tuple[tuple[str, str], ...]
    source: str  # 認識した日本語
    spoken_at: float  # 話し始めた時刻 (UNIX 時刻, 秒)

    @property
    def text(self) -> str:
        """ログ表示用に1行にまとめたもの。"""
        return " / ".join(text for _, text in self.lines)

    def text_in(self, lang: str) -> str:
        return " ".join(text for line_lang, text in self.lines if line_lang == lang)


class QueuedSink:
    """字幕をキューに積み、専用タスクで順番に送る。送信の遅れが他の送り先に波及しない。"""

    name = "sink"

    def __init__(self, enabled: bool = True, max_queue: int = 20):
        self.enabled = enabled
        self._queue: asyncio.Queue[Caption] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None
        self.sent = 0
        self.skipped = 0

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._worker(), name=f"{self.name}-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def submit(self, caption: Caption) -> None:
        if not self.enabled:
            return
        if self._queue.full():
            # 送信が詰まっているときは古い字幕を捨てる(遅れた字幕は役に立たない)
            try:
                self._queue.get_nowait()
                self.skipped += 1
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(caption)

    async def _worker(self) -> None:
        while True:
            caption = await self._queue.get()
            try:
                await self.send(caption)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 1件の失敗で止まらないようにする
                self.last_error = str(exc)
                log.warning("%s: 送信に失敗しました: %s", self.name, exc)

    async def send(self, caption: Caption) -> None:
        raise NotImplementedError

    def status(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "sent": self.sent,
            "skipped": self.skipped,
            "last_error": self.last_error,
        }
