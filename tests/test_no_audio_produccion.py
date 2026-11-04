"""El cliente requiere captura exclusivamente de vídeo, nunca audio."""
import pytest
from gpia import config, video


def test_config_rechaza_activar_audio():
    cfg = config._deep_merge(config.DEFAULTS, {"audio": {"enabled": True}})
    with pytest.raises(ValueError, match="audio"):
        config.validate(cfg)


def test_ffmpeg_rechaza_audio_en_ambos_sistemas():
    for platform in ("win32", "x11"):
        with pytest.raises(ValueError, match="audio"):
            video.build_command("ffmpeg", "output.mp4", fps=10, crf=23,
                                preset="ultrafast", platform=platform, audio=True)
