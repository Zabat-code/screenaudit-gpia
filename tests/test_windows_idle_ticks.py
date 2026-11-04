"""Regresiones del reloj de inactividad Win32 (APIs simuladas)."""
import pytest
from gpia.backends import windows


class Func:
    def __init__(self, f):
        self.f = f
    def __call__(self, *args):
        return self.f(*args)


def setup(monkeypatch, now, last, ok=True):
    class User:
        pass
    class Kernel:
        pass
    user, kernel = User(), Kernel()
    def get_input(pointer):
        pointer._obj.dwTime = last
        return int(ok)
    user.GetLastInputInfo = Func(get_input)
    kernel.GetTickCount64 = Func(lambda: now)
    monkeypatch.setattr(windows, 'user32', user)
    monkeypatch.setattr(windows, 'kernel32', kernel)
    return windows.Win32Backend.__new__(windows.Win32Backend)


def test_inactividad_cruza_desbordamiento_dword(monkeypatch):
    backend = setup(monkeypatch, 0x200000020, 0xFFFFFFF0)
    assert backend.idle_seconds() == pytest.approx(0.048)


def test_error_win32_no_finge_actividad(monkeypatch):
    backend = setup(monkeypatch, 1000, 900, ok=False)
    with pytest.raises(OSError):
        backend.idle_seconds()
