"""Respeta el destino de sesiones configurado por el arquitecto."""
import pytest
from gpia import config
from gpia.backends.null import NullBackend
from gpia.recorder import Session


@pytest.mark.xfail(strict=True, reason='output.root aún no es usado por captura ni recuperación')
def test_sesiones_usan_raiz_explicita_sin_salir_del_servicio(tmp_path):
    cfg = config._deep_merge(config.DEFAULTS, {
        'output': {'root': str(tmp_path)}, 'session': {'service': '606'}
    })
    session = Session(cfg, NullBackend(), video=False)
    assert tmp_path.resolve() in __import__('pathlib').Path(session.out_dir).resolve().parents
    assert __import__('pathlib').Path(session.out_dir).parent.name == '606'
