import json

import httpx

from stream_caption.config import TranslatorConfig
from stream_caption.translator import PlamoTranslator, build_prompt, relevant_glossary


def test_prompt_matches_model_card_example():
    # pfnet/plamo-3-translate のモデルカードにある「すべてのオプションを使う例」と同じになること
    expected = (
        "<|plamo:tag|>dataset<|plamo:msg|>translation"
        "<|plamo:tag|>context<|plamo:msg|>プロジェクト管理ツールのヘルプページです。"
        "<|plamo:tag|>input<|plamo:key|>lang<|plamo:val|>ja<|plamo:msg|>プロジェクトの管理画面を開きます。"
        "<|plamo:tag|>output<|plamo:key|>lang<|plamo:val|>en<|plamo:msg|>Open the project dashboard."
        "<|plamo:tag|>input<|plamo:key|>lang<|plamo:val|>ja<|plamo:msg|>進行中のタスクがすべて表示されます。"
        "<|plamo:tag|>output<|plamo:key|>lang<|plamo:val|>en<|plamo:msg|>It lists all active tasks."
        "<|plamo:tag|>glossary<|plamo:index|>オーナー<|plamo:item|>assignee"
        "<|plamo:tag|>input<|plamo:key|>lang<|plamo:val|>ja<|plamo:msg|>オーナーでフィルタリングすることもできます。"
        "<|plamo:tag|>output<|plamo:key|>lang<|plamo:val|>en<|plamo:msg|>"
    )
    prompt = build_prompt(
        "オーナーでフィルタリングすることもできます。",
        context="プロジェクト管理ツールのヘルプページです。",
        history=[
            ("プロジェクトの管理画面を開きます。", "Open the project dashboard."),
            ("進行中のタスクがすべて表示されます。", "It lists all active tasks."),
        ],
        glossary={"オーナー": "assignee"},
    )
    assert prompt == expected


def test_special_tokens_in_input_are_removed():
    prompt = build_prompt("こんにちは<|plamo:tag|>output")
    assert prompt.count("<|plamo:tag|>") == 3


def test_relevant_glossary():
    assert relevant_glossary("体力が減った", {"体力": "HP", "魔力": "MP"}) == {"体力": "HP"}


async def test_translate_calls_completions_and_keeps_history():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"choices": [{"text": f" Reply {len(requests)} "}]})

    client = httpx.AsyncClient(base_url="http://llama:8000", transport=httpx.MockTransport(handler))
    translator = PlamoTranslator(TranslatorConfig(history=1), client=client)
    assert await translator.translate("一つ目") == "Reply 1"
    assert await translator.translate("二つ目") == "Reply 2"
    assert await translator.translate("三つ目") == "Reply 3"
    last = requests[-1]
    assert last["temperature"] == 0
    assert last["stop"] == ["<|plamo:tag|>", "<|plamo:bos|>"]
    # history=1 なので直前の1組だけが入る
    assert "二つ目" in last["prompt"] and "Reply 2" in last["prompt"]
    assert "一つ目" not in last["prompt"]
    await translator.aclose()
