import pytest

from stream_caption.config import load_config


def test_yaml_and_env(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "mode: en\n"
        "asr:\n  vocabulary:\n    エルデンリング: [えるでんりんぐ]\n"
        "translator:\n  glossary:\n    体力: HP\n"
        "sinks:\n  obs:\n    port: 4456\n",
        encoding="utf-8",
    )
    config = load_config(path, env={"CAPTION_MODE": "ja", "OBS_PASSWORD": "pw"})
    assert config.mode == "ja"
    assert config.asr.vocabulary == {"エルデンリング": ["えるでんりんぐ"]}
    assert config.translator.glossary == {"体力": "HP"}
    assert config.sinks.obs.port == 4456
    assert config.sinks.obs.password == "pw"


def test_unknown_key_is_an_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("segmenter:\n  pause_second: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pause_second"):
        load_config(path, env={})


def test_missing_file_uses_defaults(tmp_path):
    config = load_config(tmp_path / "none.yaml", env={})
    assert config.mode == "en" and config.sinks.obs.enabled


def test_example_config_is_valid():
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"
    config = load_config(example, env={})
    assert config.mode in ("en", "ja")
