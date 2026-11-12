import numpy as np
from helpers import SR, frames, silence, tone

from stream_caption.config import SegmenterConfig
from stream_caption.segmenter import PhraseSegmenter


def run(segmenter, audio):
    out = []
    for frame in frames(audio):
        out.extend(segmenter.push(frame))
    return out


def test_phrase_ends_after_pause():
    seg = PhraseSegmenter(SegmenterConfig())
    phrases = run(seg, np.concatenate([silence(1.0), tone(1.5), silence(1.0)]))
    assert len(phrases) == 1
    p = phrases[0]
    assert p.start_sample == SR  # 先頭の無音は捨てられ、発話の位置から始まる
    # 1.5秒の発話 + 末尾の無音0.2秒だけ残る
    assert abs(p.seconds - 1.7) < 0.11


def test_short_noise_is_dropped():
    seg = PhraseSegmenter(SegmenterConfig())
    assert run(seg, np.concatenate([tone(0.2), silence(1.0)])) == []


def test_no_phrase_until_pause():
    seg = PhraseSegmenter(SegmenterConfig())
    assert run(seg, tone(2.0)) == []
    assert len(seg.flush()) == 1


def test_long_speech_is_cut_and_rest_is_kept():
    config = SegmenterConfig(max_phrase_seconds=5.0, cut_search_seconds=2.0)
    seg = PhraseSegmenter(config)
    # 4秒しゃべって 0.3秒だけ小さい声(無音扱いにならない程度)、さらに3秒
    quiet = tone(0.3, amplitude=0.02)
    audio = np.concatenate([tone(4.0), quiet, tone(3.0), silence(1.0)])
    phrases = run(seg, audio)
    assert len(phrases) == 2
    first, second = phrases
    # 静かな区間(4.0〜4.3秒)で切られる
    assert 3.9 < first.seconds < 4.4
    assert second.start_sample == first.start_sample + first.audio.size
    total = first.audio.size + second.audio.size
    assert abs(total / SR - 7.5) < 0.15  # 取りこぼしなし(末尾の無音0.2秒込み)


def test_threshold_follows_dbfs_setting():
    seg = PhraseSegmenter(SegmenterConfig(silence_dbfs=-20))
    # -26 dBFS 程度の音は -20 dBFS のしきい値では無音扱い
    assert run(seg, np.concatenate([tone(1.5, amplitude=0.07), silence(1.0)])) == []
