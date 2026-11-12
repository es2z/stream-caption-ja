"""字幕テキストの整形と分割。"""

from __future__ import annotations

import re
import unicodedata

# CEA-608 の基本文字セットにない記号を ASCII に置き換える
_ASCII_MAP = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
    "…": "...",
    " ": " ",
    "　": " ",
    "「": '"',
    "」": '"',
    "、": ",",
    "。": ".",
    "！": "!",
    "？": "?",
}

_JA_BREAKS = "。、！？!?…"


def to_cea608_text(text: str) -> str:
    """CEA-608 で送れる文字だけにする。日本語などの送れない文字は落とす。"""
    text = "".join(_ASCII_MAP.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKC", text)
    text = "".join(_ASCII_MAP.get(ch, ch) for ch in text)
    # NFKD で分解してアクセント記号を外し、ASCII の印字可能文字だけ残す
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in decomposed if 0x20 <= ord(ch) < 0x7F)
    return re.sub(r"\s+", " ", ascii_only).strip()


def has_non_latin(text: str) -> bool:
    """ラテン文字以外の文字(かな・漢字など)を含むか。記号は数えない。"""
    return any(ord(ch) > 0x24F and ch.isalpha() for ch in text)


def split_caption(text: str, max_chars: int) -> list[str]:
    """字幕を max_chars 以下の塊に分ける。英語は単語の境目、日本語は句読点を優先して切る。"""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > max_chars:
        window = rest[: max_chars + 1]
        cut = -1
        # 文末・句読点の直後で切る
        for i in range(len(window) - 1, max_chars // 4, -1):
            if window[i - 1] in _JA_BREAKS or (window[i - 1] in ".,;:" and window[i] == " "):
                cut = i
                break
        if cut == -1:
            space = window.rfind(" ")
            cut = space if space > max_chars // 3 else max_chars
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return [c for c in chunks if c]
