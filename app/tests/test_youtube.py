from urllib.parse import parse_qs, urlsplit

import httpx

from stream_caption.config import CaptionConfig, YouTubeSinkConfig
from stream_caption.sinks import Caption, YouTubeCaptionSink
from stream_caption.sinks.youtube import build_body, format_timestamp, mask_url, parse_timestamp

URL = "http://upload.youtube.com/closedcaption?cid=SECRET-KEY"


def test_timestamp_format():
    assert format_timestamp(0) == "1970-01-01T00:00:00.000"
    assert format_timestamp(1.2345) == "1970-01-01T00:00:01.234"
    assert parse_timestamp("1970-01-01T00:00:01.500") == 1.5
    assert parse_timestamp("garbage") is None


def test_body_format():
    assert build_body([(0, "Hello"), (1, "こんにちは")]) == (
        "1970-01-01T00:00:00.000\nHello\n1970-01-01T00:00:01.000\nこんにちは\n"
    )


def test_mask_url_hides_key():
    assert "SECRET" not in mask_url(URL)


async def test_send_posts_with_seq_and_utf8():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="1970-01-01T00:00:10.000")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = YouTubeCaptionSink(
        YouTubeSinkConfig(enabled=True, ingestion_url=URL), CaptionConfig(max_chars=64), client=client
    )
    await sink.send(Caption(text="今日も配信を始めます", source="", lang="ja", spoken_at=5.0))
    await sink.send(Caption(text="Second", source="", lang="en", spoken_at=6.0))
    assert [parse_qs(urlsplit(str(r.url)).query)["seq"] for r in seen] == [["1"], ["2"]]
    assert parse_qs(urlsplit(str(seen[0].url)).query)["cid"] == ["SECRET-KEY"]
    assert seen[0].content.decode("utf-8") == "1970-01-01T00:00:05.000\n今日も配信を始めます\n"
    assert "charset=utf-8" in seen[0].headers["content-type"]
    assert sink.sent == 2
    # 応答のサーバー時刻(1970年)との差で時計合わせが行われる
    assert sink.clock_offset < -1_000_000
    await client.aclose()


async def test_missing_url_is_skipped():
    sink = YouTubeCaptionSink(YouTubeSinkConfig(enabled=True), CaptionConfig())
    await sink.send(Caption(text="x", source="", lang="en", spoken_at=0))
    assert sink.skipped == 1 and sink.last_error
    await sink.stop()
