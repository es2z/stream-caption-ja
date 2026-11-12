from stream_caption.captions import has_non_latin, split_caption, to_cea608_text


def test_cea608_text_replaces_typographic_symbols():
    assert to_cea608_text("It’s “great” — really…") == 'It\'s "great" - really...'


def test_cea608_text_drops_japanese_and_accents():
    assert to_cea608_text("Café 日本") == "Cafe"


def test_has_non_latin():
    assert has_non_latin("こんにちは")
    assert not has_non_latin("Hello, café!")


def test_split_english_on_words():
    text = "This is a fairly long English sentence that needs to be split into chunks."
    chunks = split_caption(text, 32)
    assert all(len(c) <= 32 for c in chunks)
    assert " ".join(chunks) == text


def test_split_prefers_sentence_end():
    chunks = split_caption("Watch out. The boss is coming from the left side now!", 32)
    assert chunks[0] == "Watch out."


def test_split_japanese_on_punctuation():
    text = "今日はこのゲームを最後までクリアしたいと思います。みなさん応援よろしくお願いします。"
    chunks = split_caption(text, 30)
    assert chunks[0].endswith("。")
    assert "".join(chunks) == text
    assert all(len(c) <= 30 for c in chunks)


def test_short_text_is_single_chunk():
    assert split_caption("  Hi  there ", 32) == ["Hi there"]
    assert split_caption("", 32) == []
