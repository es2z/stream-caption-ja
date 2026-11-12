import asyncio

from fake_obs import FakeObs

from stream_caption.config import CaptionConfig, ObsSinkConfig
from stream_caption.sinks import Caption, ObsCaptionSink


def make_sink(port: int, password: str, max_chars: int = 64) -> ObsCaptionSink:
    config = ObsSinkConfig(
        enabled=True, host="127.0.0.1", port=port, password=password, reconnect_seconds=0.1
    )
    return ObsCaptionSink(config, CaptionConfig(max_chars=max_chars, chars_per_second=1000))


def caption(text: str) -> Caption:
    return Caption(lines=(("en", text),), source="", spoken_at=0)


async def test_sends_caption_with_auth():
    async with FakeObs("secret") as obs:
        sink = make_sink(obs.port, "secret")
        await sink.start()
        assert await sink.client.wait_connected(3)
        await sink.send(caption("It’s working."))
        assert obs.captions == ["It's working."]
        assert sink.sent == 1
        await sink.stop()


async def test_long_caption_is_split():
    async with FakeObs("") as obs:
        sink = make_sink(obs.port, "", max_chars=32)
        await sink.start()
        assert await sink.client.wait_connected(3)
        await sink.send(caption("This sentence is definitely longer than thirty two characters."))
        assert len(obs.captions) >= 2 and all(len(c) <= 32 for c in obs.captions)
        assert " ".join(obs.captions) == "This sentence is definitely longer than thirty two characters."
        await sink.stop()


async def test_wrong_password_does_not_connect():
    async with FakeObs("secret") as obs:
        sink = make_sink(obs.port, "wrong")
        await sink.start()
        assert not await sink.client.wait_connected(1)
        await sink.send(caption("Hello"))
        assert obs.captions == [] and sink.skipped == 1
        await sink.stop()


async def test_not_streaming_is_reported_not_raised():
    async with FakeObs("") as obs:
        obs.streaming = False
        sink = make_sink(obs.port, "")
        await sink.start()
        assert await sink.client.wait_connected(3)
        await sink.send(caption("Hello"))
        assert sink.streaming_warning and "配信中ではない" in sink.last_error
        await sink.stop()


async def test_japanese_is_not_sent_to_obs():
    async with FakeObs("") as obs:
        sink = make_sink(obs.port, "")
        await sink.start()
        assert await sink.client.wait_connected(3)
        await sink.send(Caption(lines=(("ja", "こんにちは"),), source="こんにちは", spoken_at=0))
        assert obs.captions == [] and sink.skipped == 1
        await sink.stop()


async def test_reconnects_after_obs_restart():
    async with FakeObs("") as obs:
        port = obs.port
        sink = make_sink(port, "")
        await sink.start()
        assert await sink.client.wait_connected(3)
    # OBS が落ちた
    await asyncio.sleep(0.3)
    assert not sink.client.connected
    await sink.stop()


async def test_bilingual_sends_only_english_to_obs():
    async with FakeObs("") as obs:
        sink = make_sink(obs.port, "")
        await sink.start()
        assert await sink.client.wait_connected(3)
        both = Caption(lines=(("ja", "こんにちは"), ("en", "Hello")), source="こんにちは", spoken_at=0)
        await sink.send(both)
        assert obs.captions == ["Hello"]
        await sink.stop()
