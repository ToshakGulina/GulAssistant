import array

from gul.audio import VAD, silence_chunk
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
