"""日本語 -> 英語の翻訳(PLaMo 3 Translate を llama-server 経由で呼ぶ)。

プロンプトはモデルカードにある PFML3 形式をそのまま組み立て、
llama-server の OpenAI 互換 /v1/completions に送る。
BOS トークンは llama.cpp が自動で付けるので含めない。
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Protocol

import httpx

from .config import TranslatorConfig

log = logging.getLogger(__name__)

TAG = "<|plamo:tag|>"
MSG = "<|plamo:msg|>"
KEY = "<|plamo:key|>"
VAL = "<|plamo:val|>"
INDEX = "<|plamo:index|>"
ITEM = "<|plamo:item|>"
STOP = ["<|plamo:tag|>", "<|plamo:bos|>"]
_SPECIAL = (TAG, MSG, KEY, VAL, INDEX, ITEM, "<|plamo:bos|>")


def _clean(text: str) -> str:
    """利用者の文字列に特殊トークンが混ざってプロンプトが壊れないようにする。"""
    for token in _SPECIAL:
        text = text.replace(token, "")
    return text.strip()


def build_prompt(
    text: str,
    *,
    source_lang: str = "ja",
    target_lang: str = "en",
    history: list[tuple[str, str]] | None = None,
    context: str = "",
    glossary: dict[str, str] | None = None,
) -> str:
    parts = [f"{TAG}dataset{MSG}translation"]
    if context.strip():
        parts.append(f"{TAG}context{MSG}{_clean(context)}")
    for source, target in history or []:
        parts.append(f"{TAG}input{KEY}lang{VAL}{source_lang}{MSG}{_clean(source)}")
        parts.append(f"{TAG}output{KEY}lang{VAL}{target_lang}{MSG}{_clean(target)}")
    if glossary:
        items = "".join(f"{INDEX}{_clean(k)}{ITEM}{_clean(v)}" for k, v in glossary.items())
        parts.append(f"{TAG}glossary{items}")
    parts.append(f"{TAG}input{KEY}lang{VAL}{source_lang}{MSG}{_clean(text)}")
    parts.append(f"{TAG}output{KEY}lang{VAL}{target_lang}{MSG}")
    return "".join(parts)


def relevant_glossary(text: str, glossary: dict[str, str]) -> dict[str, str]:
    """今回の文に出てくる用語だけを渡す(プロンプトを短く保ち、無関係な訳語の混入を防ぐ)。"""
    return {k: v for k, v in glossary.items() if k and k in text}


class Translator(Protocol):
    async def translate(self, text: str) -> str: ...

    def reset_history(self) -> None: ...

    async def aclose(self) -> None: ...


class PlamoTranslator:
    def __init__(self, config: TranslatorConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"), timeout=config.timeout_seconds
        )
        self._history: deque[tuple[str, str]] = deque(maxlen=max(0, config.history))

    def reset_history(self) -> None:
        self._history.clear()

    async def translate(self, text: str) -> str:
        prompt = build_prompt(
            text,
            history=list(self._history),
            context=self.config.context,
            glossary=relevant_glossary(text, self.config.glossary),
        )
        response = await self._client.post(
            "/v1/completions",
            json={
                "model": self.config.model,
                "prompt": prompt,
                "temperature": 0,
                "max_tokens": self.config.max_tokens,
                "stop": STOP,
            },
        )
        response.raise_for_status()
        data = response.json()
        result = (data.get("choices") or [{}])[0].get("text", "")
        result = _clean(result)
        if result and self._history.maxlen:
            self._history.append((text, result))
        return result

    async def ready(self) -> bool:
        try:
            response = await self._client.get("/health", timeout=3)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
