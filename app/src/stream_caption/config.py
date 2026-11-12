"""設定の読み込み。

非秘密の設定は config.yaml、秘密情報(OBSのパスワード、YouTubeの字幕URL)は環境変数から読む。
環境変数は config.yaml より優先される。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

MODES = ("en", "ja")


@dataclass
class AsrConfig:
    backend: str = "haas"  # haas | dummy
    model: str = "third-intelligence/Haas-ja-v1.0"
    device: str = "cuda"
    # 表記 -> 読み(カナ)のリスト。ゲーム名・キャラ名など、誤認識しやすい固有名詞を登録する。
    vocabulary: dict[str, list[str]] = field(default_factory=dict)
    vocabulary_alpha: float = 2.0


@dataclass
class SegmenterConfig:
    pause_seconds: float = 0.7  # この長さの無音で1文を確定する
    max_phrase_seconds: float = 14.0  # 無音がなくてもこの長さで区切る
    cut_search_seconds: float = 3.0
    min_audio_seconds: float = 0.8
    min_speech_seconds: float = 0.3
    silence_dbfs: float = -38.0  # これより小さい音量は無音扱い


@dataclass
class TranslatorConfig:
    base_url: str = "http://llama:8000"
    model: str = "plamo-3-translate"
    history: int = 3  # 直近の原文・訳文を何組プロンプトに含めるか
    context: str = ""  # 配信の説明など(訳の安定に効く)
    glossary: dict[str, str] = field(default_factory=dict)  # 日本語 -> 英語の訳語固定
    max_tokens: int = 256
    timeout_seconds: float = 10.0


@dataclass
class FilterConfig:
    drop_texts: list[str] = field(default_factory=list)  # これだけの発話は字幕にしない
    min_chars: int = 2


@dataclass
class CaptionConfig:
    max_chars: int = 64  # 1回に送る最大文字数(CEA-608は1行32文字)
    chars_per_second: float = 17.0  # 分割送信時の間隔の目安


@dataclass
class ObsSinkConfig:
    enabled: bool = True
    host: str = "host.docker.internal"
    port: int = 4455
    password: str = ""
    reconnect_seconds: float = 5.0


@dataclass
class YouTubeSinkConfig:
    enabled: bool = False
    ingestion_url: str = ""


@dataclass
class SinksConfig:
    obs: ObsSinkConfig = field(default_factory=ObsSinkConfig)
    youtube: YouTubeSinkConfig = field(default_factory=YouTubeSinkConfig)


@dataclass
class AppConfig:
    mode: str = "en"  # en: 英語に翻訳 / ja: 日本語のまま
    asr: AsrConfig = field(default_factory=AsrConfig)
    segmenter: SegmenterConfig = field(default_factory=SegmenterConfig)
    translator: TranslatorConfig = field(default_factory=TranslatorConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    captions: CaptionConfig = field(default_factory=CaptionConfig)
    sinks: SinksConfig = field(default_factory=SinksConfig)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")


def _build(cls: type, data: dict[str, Any] | None) -> Any:
    """dict から dataclass を組み立てる。未知のキーはエラーにして設定ミスに気付けるようにする。"""
    data = data or {}
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"unknown keys in {cls.__name__}: {sorted(unknown)}")
    kwargs = {}
    for name, value in data.items():
        default = known[name].default_factory() if callable(known[name].default_factory) else None
        if value is None and default is not None:
            # YAML でキーだけ書いて中身をコメントアウトした場合など
            kwargs[name] = default
        elif is_dataclass(default) and isinstance(value, dict):
            kwargs[name] = _build(type(default), value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def _env_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_config(path: str | os.PathLike | None = None, env: dict[str, str] | None = None) -> AppConfig:
    env = dict(os.environ if env is None else env)
    path = Path(path or env.get("CONFIG_PATH", "/config/config.yaml"))
    data: dict[str, Any] = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = _build(AppConfig, data)

    # 環境変数による上書き(秘密情報と、よく切り替えるもの)
    if env.get("CAPTION_MODE"):
        config.mode = env["CAPTION_MODE"].strip()
        config.__post_init__()
    if env.get("ASR_BACKEND"):
        config.asr.backend = env["ASR_BACKEND"].strip()
    if env.get("TRANSLATOR_URL"):
        config.translator.base_url = env["TRANSLATOR_URL"].strip()
    if "OBS_PASSWORD" in env:
        config.sinks.obs.password = env["OBS_PASSWORD"]
    if env.get("OBS_HOST"):
        config.sinks.obs.host = env["OBS_HOST"].strip()
    if env.get("OBS_PORT"):
        config.sinks.obs.port = int(env["OBS_PORT"])
    if env.get("OBS_ENABLED"):
        config.sinks.obs.enabled = _env_bool(env["OBS_ENABLED"])
    if env.get("YOUTUBE_CAPTION_URL"):
        config.sinks.youtube.ingestion_url = env["YOUTUBE_CAPTION_URL"].strip()
    if env.get("YOUTUBE_ENABLED"):
        config.sinks.youtube.enabled = _env_bool(env["YOUTUBE_ENABLED"])
    return config
