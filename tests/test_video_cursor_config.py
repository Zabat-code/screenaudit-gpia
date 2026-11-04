"""Los parámetros de cursor deben afectar a la entrada FFmpeg."""
import pytest
from gpia.video import build_command


@pytest.mark.parametrize('platform', ['win32', 'x11'])
@pytest.mark.parametrize('draw', [True, False])
def test_cursor_se_configura_antes_de_la_entrada(platform, draw):
    cmd = build_command('ffmpeg', 'out.mp4', fps=10, crf=23,
                        preset='ultrafast', platform=platform, draw_mouse=draw)
    assert cmd.index('-draw_mouse') < cmd.index('-i')
    assert cmd[cmd.index('-draw_mouse') + 1] == ('1' if draw else '0')
