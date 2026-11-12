"""音声をフレーズ(1文)単位に区切る。

Haas-ASR の demos/segmenter.py (Apache-2.0, Third Intelligence) を元にしている。
元のデモは話している途中の文を 250ms ごとに読み直すが、ここでは
「確定した文だけを1回読む」ので、GPUを使うのは文が確定した瞬間だけになる。

- 無音のあとの最初の発話から文が始まる(前の無音は捨てる)。
- `pause_seconds` の無音で文が確定する。
- 無音がなくても `max_phrase_seconds` に達したら、直近 `cut_search_seconds` の
  いちばん静かな位置で切り、残りは次の文の頭になる。
- `min_audio_seconds` 以上の長さと `min_speech_seconds` 以上の発話がない断片は捨てる。

発話と無音の判定は、フレームごとの RMS 音量で行う。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import SegmenterConfig

SAMPLE_RATE = 16_000


def dbfs_to_rms(dbfs: float) -> float:
    return float(10 ** (dbfs / 20))


@dataclass(frozen=True)
class Phrase:
    """確定した1文: 音声と、セッション開始からの開始位置(サンプル数)。"""

    audio: np.ndarray
    start_sample: int

    @property
    def seconds(self) -> float:
        return self.audio.size / SAMPLE_RATE


class PhraseSegmenter:
    def __init__(self, config: SegmenterConfig | None = None, sample_rate: int = SAMPLE_RATE):
        self.config = config or SegmenterConfig()
        self.sample_rate = sample_rate
        self.silence_rms = dbfs_to_rms(self.config.silence_dbfs)
        self._frames: list[np.ndarray] = []
        self._length = 0  # 開いている文のサンプル数
        self._speech = 0  # そのうち発話のサンプル数
        self._silence = 0  # 末尾の連続した無音のサンプル数
        self._received = 0  # セッションで受け取った総サンプル数
        self._start = 0  # 開いている文の開始位置
        self.last_rms = 0.0

    @property
    def received_samples(self) -> int:
        return self._received

    def push(self, frame: np.ndarray) -> list[Phrase]:
        """16kHz モノラル float32 のフレームを1つ追加し、確定した文を返す。"""
        if frame.size == 0:
            return []
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        self.last_rms = rms
        silent = rms < self.silence_rms
        if silent and self._speech == 0:
            self._received += frame.size
            self._length = 0
            self._frames = []
            return []
        if self._length == 0:
            self._start = self._received
        self._frames.append(frame.astype(np.float32, copy=False))
        self._length += frame.size
        self._received += frame.size
        pause = self.config.pause_seconds * self.sample_rate
        if silent:
            self._silence += frame.size
            if self._silence >= pause:
                if self._readable():
                    return [self._take_all()]
                # 短い雑音のあとに無音: 文ではないので捨てる
                self._reset(self._received)
        else:
            self._silence = 0
            self._speech += frame.size
            if self._length >= self.config.max_phrase_seconds * self.sample_rate:
                return [self._take_until_quiet_cut()]
        return []

    def flush(self) -> list[Phrase]:
        """停止時など、開いている文を強制的に確定する。"""
        if self._readable():
            return [self._take_all()]
        self._reset(self._received)
        return []

    # -- 内部処理 ---------------------------------------------------------------

    def _audio(self) -> np.ndarray:
        if len(self._frames) > 1:
            self._frames = [np.concatenate(self._frames)]
        return self._frames[0] if self._frames else np.zeros(0, dtype=np.float32)

    def _readable(self) -> bool:
        return (
            self._length >= self.config.min_audio_seconds * self.sample_rate
            and self._speech >= self.config.min_speech_seconds * self.sample_rate
        )

    def _take_all(self) -> Phrase:
        audio, start = self._audio(), self._start
        # 末尾の無音は認識に不要なので、少し(0.2秒)だけ残して落とす
        keep_tail = int(0.2 * self.sample_rate)
        trim = max(0, self._silence - keep_tail)
        if trim:
            audio = audio[: audio.size - trim]
        self._reset(self._received)
        return Phrase(audio, start)

    def _take_until_quiet_cut(self) -> Phrase:
        audio, start = self._audio(), self._start
        cut = self._quiet_cut(audio)
        rest = audio[cut:]
        self._reset(start + cut)
        if rest.size:
            self._frames = [rest]
            self._length = self._speech = rest.size
        return Phrase(audio[:cut], start)

    def _reset(self, start: int) -> None:
        self._frames, self._length, self._speech, self._silence = [], 0, 0, 0
        self._start = start

    def _quiet_cut(self, audio: np.ndarray) -> int:
        """直近 cut_search_seconds 内で、100ms 窓のエネルギーが最小になる位置の中心。"""
        window = self.sample_rate // 10
        first = max(0, audio.size - int(self.config.cut_search_seconds * self.sample_rate))
        span = audio[first:].astype(np.float64)
        if span.size <= window:
            return audio.size
        energy = np.cumsum(np.concatenate([[0.0], span**2]))
        offset = int(np.argmin(energy[window:] - energy[:-window]))
        return first + offset + window // 2
