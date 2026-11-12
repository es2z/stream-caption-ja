"""YouTube へ字幕を HTTP POST で直接送る。

YouTube Studio の配信設定で字幕を「POST 字幕を URL に送信」にし、
表示される字幕取り込み URL を YOUTUBE_CAPTION_URL に設定する。
こちらは日本語も送れる。

本文の形式: 1行目に UTC のタイムスタンプ(末尾の Z なし)、2行目に字幕。
複数の字幕を1回の POST にまとめられる。リクエストごとに seq を1ずつ増やす。
タイムスタンプは YouTube のサーバー時刻から60秒以内である必要がある。
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from ..captions import split_caption
from ..config import CaptionConfig, YouTubeSinkConfig
from .base import Caption, QueuedSink

log = logging.getLogger(__name__)


def format_timestamp(unix_seconds: float) -> str:
    dt = datetime.fromtimestamp(unix_seconds, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def parse_timestamp(text: str) -> float | None:
    """YouTube の応答本文(サーバー時刻)を UNIX 時刻にする。読めなければ None。"""
    text = text.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
    return None


def build_body(cues: list[tuple[float, str]]) -> str:
    return "".join(f"{format_timestamp(ts)}\n{text}\n" for ts, text in cues)


def with_seq(url: str, seq: int) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "seq"]
    query.append(("seq", str(seq)))
    return urlunsplit(parts._replace(query=urlencode(query)))


def mask_url(url: str) -> str:
    """ログや画面に出すとき用。URL には配信キー相当の情報が入るので隠す。"""
    if not url:
        return ""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}?…"


class YouTubeCaptionSink(QueuedSink):
    name = "youtube"

    def __init__(
        self,
        config: YouTubeSinkConfig,
        captions: CaptionConfig,
        client: httpx.AsyncClient | None = None,
    ):
        super().__init__(enabled=config.enabled)
        self.ingestion_url = config.ingestion_url
        self.captions = captions
        self._client = client or httpx.AsyncClient(timeout=10)
        self.seq = 1
        self.clock_offset = 0.0  # YouTube のサーバー時刻 - 手元の時刻 (秒)

    def set_url(self, url: str) -> None:
        if url != self.ingestion_url:
            self.ingestion_url = url
            self.seq = 1

    async def stop(self) -> None:
        await super().stop()
        await self._client.aclose()

    async def heartbeat(self) -> bool:
        """空の POST で疎通確認と時刻合わせをする(seq は増やさない)。"""
        if not self.ingestion_url:
            return False
        before = time.time()
        response = await self._client.post(
            with_seq(self.ingestion_url, self.seq),
            content=b"",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        self._sync_clock(response, before)
        return response.is_success

    def _sync_clock(self, response: httpx.Response, sent_at: float) -> None:
        server = parse_timestamp(response.text) if response.is_success else None
        if server is not None:
            self.clock_offset = server - (sent_at + time.time()) / 2

    async def send(self, caption: Caption) -> None:
        if not self.ingestion_url:
            self.skipped += 1
            self.last_error = "YOUTUBE_CAPTION_URL が設定されていません"
            return
        chunks = split_caption(caption.text, self.captions.max_chars)
        if not chunks:
            return
        cues, at = [], caption.spoken_at + self.clock_offset
        for chunk in chunks:
            cues.append((at, chunk))
            at += len(chunk) / max(1.0, self.captions.chars_per_second)
        before = time.time()
        response = await self._client.post(
            with_seq(self.ingestion_url, self.seq),
            content=build_body(cues).encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        if not response.is_success:
            raise RuntimeError(f"YouTube が {response.status_code} を返しました: {response.text[:200]}")
        self.seq += 1
        self.sent += 1
        self.last_error = None
        self._sync_clock(response, before)

    def status(self) -> dict:
        status = super().status()
        status.update(
            url=mask_url(self.ingestion_url), seq=self.seq, clock_offset=round(self.clock_offset, 3)
        )
        return status
