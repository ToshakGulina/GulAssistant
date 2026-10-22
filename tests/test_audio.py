import array

import pytest

from gul.audio import VAD, analyze_pcm, peak_level, rms_level, silence_chunk
from gul.config import load_config
from gul.tts import Speaker, TTSEngine


def test_vad_fixed_threshold():
    vad = VAD(threshold=100)
    assert vad.is_speech(silence_chunk(100)) is False
    loud = array.array("h", [2000] * 2000).tobytes()
    assert vad.is_speech(loud) is True


def test_vad_adaptive():
    vad = VAD()
    for _ in range(12):
        vad.is_speech(silence_chunk(100))
    assert vad.is_speech(silence_chunk(100)) is False
    loud = array.array("h", [3000] * 2000).tobytes()
    assert vad.is_speech(loud) is True


class DummyPlayer:
    def __init__(self):
        self.played = []
        self.stopped = False

    def play_file(self, path, blocking=True, timeout=None):
        self.played.append(str(path))
        return True

    def beep(self, *args, **kwargs):
        return None

    def stop(self):
        self.stopped = True

    def available(self):
        return True

    def is_playing(self):
        return False


class DummyEngine(TTSEngine):
    name = "dummy"
    title = "Dummy"

    def available(self):
        return True

    def synthesize(self, text):
        import wave

        path = self._temp_path(".wav")
        with wave.open(str(path), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(16000)
            fh.writeframes(b"\x00\x00" * 1600)
        return path


def test_speaker_stream_pipeline():
    cfg = load_config()
    player = DummyPlayer()
    speaker = Speaker(cfg, player=player)
    speaker.engines = [DummyEngine(cfg)]
    full = speaker.say_stream(iter(["Привет. ", "Как дела? ", "Всё хорошо!"]))
    assert "Привет" in full
    assert len(player.played) >= 2  # хотя бы два предложения озвучены


def test_speaker_say():
    cfg = load_config()
    player = DummyPlayer()
    speaker = Speaker(cfg, player=player)
    speaker.engines = [DummyEngine(cfg)]
    assert speaker.say("Проверка связи") is True
    assert player.played


def _tone(amplitude: int, seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Простая «речь»: чередование тишины и громких участков."""
    import math

    frames = array.array("h")
    total = int(sample_rate * seconds)
    for i in range(total):
        loud = (i // (sample_rate // 4)) % 2 == 1
        value = int(amplitude * math.sin(2 * math.pi * 220 * i / sample_rate)) if loud else 0
        frames.append(value)
    return frames.tobytes()


def test_rms_and_peak_of_silence():
    assert rms_level(silence_chunk(200)) == 0.0
    assert peak_level(silence_chunk(200)) == 0


def test_rms_and_peak_of_tone():
    pcm = _tone(4000)
    # Половина времени — тишина, половина — синус с пиком 4000.
    assert 1900 < rms_level(pcm) < 2900
    assert peak_level(pcm) > 3500


def test_analyze_pcm_detects_speech():
    info = analyze_pcm(_tone(4000, seconds=2.0))
    assert info["peak"] > 3000
    assert info["speech_ratio"] > 0.4
    assert info["clipped_ratio"] == 0.0
    assert info["seconds"] > 1.5


def test_analyze_pcm_detects_silence():
    info = analyze_pcm(silence_chunk(2000))
    assert info["peak"] == 0
    assert info["speech_ratio"] == 0.0


def test_analyze_pcm_detects_clipping():
    info = analyze_pcm(_tone(32767, seconds=1.0))
    assert info["clipped_ratio"] > 0.2


def test_mic_test_reports_silence_as_problem(cfg, monkeypatch, capsys):
    import gul.audio as audio
    import gul.doctor as doctor

    monkeypatch.setattr(audio, "which", lambda name: "/usr/bin/arecord")
    monkeypatch.setattr(audio, "record_pcm", lambda *a, **k: silence_chunk(2000))
    code = doctor.mic_test(cfg, seconds=2, keep=False)
    out = capsys.readouterr().out
    assert code == 1
    assert "молчит" in out


def test_mic_test_accepts_good_signal(cfg, monkeypatch, capsys):
    import gul.audio as audio
    import gul.doctor as doctor

    monkeypatch.setattr(audio, "which", lambda name: "/usr/bin/arecord")
    monkeypatch.setattr(audio, "record_pcm", lambda *a, **k: _tone(3000, seconds=2.0))
    code = doctor.mic_test(cfg, seconds=2, keep=False)
    out = capsys.readouterr().out
    assert code == 0
    assert "работает" in out
