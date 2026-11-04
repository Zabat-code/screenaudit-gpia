"""Pruebas de arranque del hook Win32 usando sustitutos del sistema operativo."""
import threading
from gpia.backends import windows


class FakeFunction:
    def __init__(self, result=None):
        self.result = result
        self.calls = []
    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class FakeWin32:
    def __init__(self, hook):
        self.SetWindowsHookExW = FakeFunction(hook)
        self.UnhookWindowsHookEx = FakeFunction(True)
        self.GetMessageW = FakeFunction(0)
        self.CallNextHookEx = FakeFunction(0)
        self.TranslateMessage = FakeFunction(0)
        self.DispatchMessageW = FakeFunction(0)
        self.PostThreadMessageW = FakeFunction(True)
        self.PeekMessageW = FakeFunction(0)


def make_backend(monkeypatch, hook):
    api = FakeWin32(hook)
    monkeypatch.setattr(windows, 'user32', api)
    class Kernel:
        GetCurrentThreadId = FakeFunction(123)
    monkeypatch.setattr(windows, 'kernel32', Kernel())
    monkeypatch.setattr(windows.ctypes, 'WINFUNCTYPE', lambda *a: lambda f: f, raising=False)
    obj = windows.Win32Backend.__new__(windows.Win32Backend)
    obj._hook_thread = None
    obj._hook_ready = threading.Event()
    obj._thread_id = None
    obj._callback_ref = None
    return obj, api


def test_no_declara_exito_si_windows_rechaza_el_hook(monkeypatch):
    backend, _ = make_backend(monkeypatch, None)
    assert backend.start_click_hook(lambda *args: None) is False
    backend.stop_click_hook()


def test_libera_el_hook_al_terminar_bucle(monkeypatch):
    backend, api = make_backend(monkeypatch, 1234)
    assert backend.start_click_hook(lambda *args: None) is True
    backend._hook_thread.join(timeout=2)
    assert api.UnhookWindowsHookEx.calls == [(1234,)]
    backend.stop_click_hook()
