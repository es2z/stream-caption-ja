"""Web サーバー全体の結線テスト(GPU・翻訳サーバー・OBS は偽物を使う)。"""

import time

import numpy as np
from fastapi.testclient import TestClient
from helpers import frames, silence, to_pcm16, tone

from stream_caption.asr import DummyRecognizer
from stream_caption.config import AppConfig
from stream_caption.main import create_app
from stream_caption.pipeline import Pipeline
from stream_caption.sinks import QueuedSink


class RecordingSink(QueuedSink):
    name = "obs"

    def __init__(self):
        super().__init__(enabled=True)
        self.received = []

    async def send(self, caption):
        self.received.append(caption)
        self.sent += 1


class FakeTranslator:
    async def translate(self, text):
        return f"EN({text})"

    def reset_history(self):
        pass

    async def aclose(self):
        pass


def make_client():
    sink = RecordingSink()

    async def factory(config: AppConfig) -> Pipeline:
        pipeline = Pipeline(config, DummyRecognizer(), FakeTranslator(), [sink])
        await pipeline.start()
        return pipeline

    return TestClient(create_app(AppConfig(), pipeline_factory=factory)), sink


def speak(ws, seconds=1.5):
    audio = np.concatenate([tone(seconds), silence(1.0)])
    for frame in frames(audio):
        ws.send_bytes(to_pcm16(frame))


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_audio_to_caption_english():
    client, sink = make_client()
    with client:
        with client.websocket_connect("/ws/events") as events:
            assert events.receive_json()["type"] == "history"
            with client.websocket_connect("/ws/audio") as ws:
                speak(ws)
                assert ws.receive_json()["type"] == "level"
                message = events.receive_json()
                while message["type"] != "caption":
                    message = events.receive_json()
        caption = message["caption"]
        assert caption["lang"] == "en"
        assert caption["text"].startswith("EN(テスト音声です")
        assert wait_for(lambda: sink.received)
        assert sink.received[0].text == caption["text"]
        # 話し始めの時刻は「今」より過去(1.5秒の発話 + 0.7秒の無音判定ぶん)
        assert time.time() - sink.received[0].spoken_at > 1.5


def test_japanese_mode_skips_translation():
    client, sink = make_client()
    with client:
        assert client.post("/api/settings", json={"mode": "ja"}).json()["mode"] == "ja"
        with client.websocket_connect("/ws/audio") as ws:
            speak(ws)
        assert wait_for(lambda: sink.received)
        assert sink.received[0].lang == "ja"
        assert sink.received[0].text.startswith("テスト音声です")


def test_invalid_mode_is_rejected():
    client, _ = make_client()
    with client:
        assert client.post("/api/settings", json={"mode": "fr"}).status_code == 400


def test_test_caption_endpoint():
    client, sink = make_client()
    with client:
        assert client.post("/api/test-caption", json={"text": "Hello"}).json() == {"ok": True}
        assert wait_for(lambda: sink.received)
        assert sink.received[0].text == "Hello"
        status = client.get("/api/status").json()
        assert status["sinks"]["obs"]["sent"] == 1


def test_index_page_served():
    client, _ = make_client()
    with client:
        response = client.get("/")
        assert response.status_code == 200 and "stream-caption-ja" in response.text
        assert client.get("/static/app.js").status_code == 200
