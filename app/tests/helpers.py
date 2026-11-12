import numpy as np

SR = 16_000


def tone(seconds: float, amplitude: float = 0.2, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def frames(audio: np.ndarray, frame_ms: int = 100):
    size = SR * frame_ms // 1000
    for i in range(0, audio.size, size):
        yield audio[i : i + size]


def to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
