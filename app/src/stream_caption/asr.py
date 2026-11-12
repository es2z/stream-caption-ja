"""音声認識(日本語)。

本番は Haas-ja-v1.0。GPU なしで動作確認するための dummy バックエンドもある。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import time
from typing import Protocol

import numpy as np

from .config import AsrConfig

log = logging.getLogger(__name__)


class Recognizer(Protocol):
    name: str

    async def transcribe(self, audio: np.ndarray) -> str: ...

    def close(self) -> None: ...


class HaasRecognizer:
    """Haas をずっと同じ1本のスレッドで動かす。

    Haas のデモによると、呼び出しのたびに別スレッドを使うと 2〜3 倍遅くなるため、
    専用スレッドに処理を渡す。呼び出しは直列になる。
    """

    def __init__(self, config: AsrConfig):
        import torch  # 重いので dummy のときは読み込まない

        torch.set_num_threads(min(8, os.cpu_count() or 1))
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="haas")
        self._model, self._vocabulary = self._executor.submit(self._load, config).result()
        device = torch.device(config.device)
        self.name = f"Haas ({torch.cuda.get_device_name(device)})" if device.type == "cuda" else "Haas (CPU)"
        self._warm_up()

    @staticmethod
    def _load(config: AsrConfig):
        import haas_asr

        log.info("Haas モデルを読み込み中: %s", config.model)
        model = haas_asr.load_model(config.model, device=config.device)
        vocabulary = None
        if config.vocabulary:
            vocabulary = haas_asr.Vocabulary(config.vocabulary, alpha=config.vocabulary_alpha)
            log.info("語彙ブースト: %d 語", len(vocabulary.words))
        return model, vocabulary

    def _run(self, audio: np.ndarray) -> str:
        import haas_asr
        import torch

        # inference_mode() は語彙ブースト(NeMo の biasing)と相性が悪く、
        # "Inference tensors do not track version counter" で落ちるため no_grad() を使う
        with torch.no_grad():
            texts = haas_asr.transcribe(self._model, [audio], batch_size=1, vocabulary=self._vocabulary)
        return texts[0].strip() if texts else ""

    def _warm_up(self) -> None:
        # 初回だけ遅くならないよう、起動時に1回動かしておく
        noise = np.random.default_rng(0).normal(0, 0.01, 16_000).astype(np.float32)
        started = time.perf_counter()
        self._executor.submit(self._run, noise).result()
        log.info("ウォームアップ完了 (%.2fs)", time.perf_counter() - started)

    async def transcribe(self, audio: np.ndarray) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._run, audio)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class DummyRecognizer:
    """GPU なしで配線を確認するための偽の認識器。音声の長さを返すだけ。"""

    name = "dummy"

    async def transcribe(self, audio: np.ndarray) -> str:
        await asyncio.sleep(0.01)
        return f"テスト音声です。長さは{audio.size / 16_000:.1f}秒でした。"

    def close(self) -> None:
        pass


def create_recognizer(config: AsrConfig) -> Recognizer:
    if config.backend == "dummy":
        return DummyRecognizer()
    if config.backend == "haas":
        return HaasRecognizer(config)
    raise ValueError(f"unknown asr backend: {config.backend!r}")
