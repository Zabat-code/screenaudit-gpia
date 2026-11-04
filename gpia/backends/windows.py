"""Win32 backend. This is the one that ships to the client.

Same interface as linux_x11.py, so gpia.recorder does not change.
Written against documented Win32 APIs only (user32/kernel32), no third-party
deps, no admin rights required: every call targets the interactive session
of the logged-in user, which is the only place ScreenAudit is installed.
"""
import ctypes
import ctypes.wintypes as wt
import os
import threading

from .base import CaptureBackend, WindowInfo, BackendUnavailable

user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None

WH_MOUSE_LL = 14
HC_ACTION = 0
WM_QUIT = 0x0012

SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77


def mouse_button_for_message(message):
    """Return the button for a down message; movement/releases are ignored."""
    return {0x0201: 1, 0x0204: 3, 0x0207: 2}.get(message)


class _MOUSEHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT),
                ("mouseData", wt.DWORD),
                ("flags", wt.DWORD),
                ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class Win32Backend(CaptureBackend):
    name = "win32"

    def __init__(self):
        if os.name != "nt":
            raise BackendUnavailable("backend win32 solo corre en Windows")
        self._hook_thread = None
        self._hook_ready = threading.Event()
        self._thread_id = None
        self._callback_ref = None

    # ---------- window ----------
    def foreground_window(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title = buf.value

        exe = self._exe_name(pid.value)
        app = exe[:-4] if exe.endswith(".exe") else exe
        return WindowInfo(hwnd=int(hwnd), pid=int(pid.value),
                          title=title, exe=exe, app=app)

    @staticmethod
    def _exe_name(pid):
        """Executable name for a pid in the current session. No admin needed."""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ""
        try:
            size = wt.DWORD(2048)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return ""
            return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)

    # ---------- idle ----------
    def idle_seconds(self):
        """Informe #3. GetLastInputInfo = idle time of keyboard+mouse."""
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT),
                        ("dwTime", wt.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        user32.GetLastInputInfo.restype = wt.BOOL
        kernel32.GetTickCount64.argtypes = []
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            raise OSError("GetLastInputInfo falló")
        # LASTINPUTINFO.dwTime is a DWORD. Compare the low 32 bits to handle
        # counter wraparound; GetTickCount64 itself must not be truncated.
        elapsed_ms = (kernel32.GetTickCount64() - info.dwTime) & 0xFFFFFFFF
        return float(elapsed_ms) / 1000.0

    # ---------- geometry ----------
    def screen_geometry(self):
        try:
            w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            if w > 0 and h > 0:
                return (w, h)
            return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        except Exception:
            return None

    # ---------- clicks ----------
    def start_click_hook(self, on_click):
        """WH_MOUSE_LL low-level hook. Needs a message loop on its own thread."""
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
        user32.SetWindowsHookExW.restype = wt.HANDLE
        user32.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
        user32.UnhookWindowsHookEx.restype = wt.BOOL

        def proc(code, wparam, lparam):
            button = mouse_button_for_message(wparam) if code == HC_ACTION else None
            if button is not None:
                try:
                    ms = ctypes.cast(lparam, ctypes.POINTER(_MOUSEHOOKSTRUCT))
                    on_click(int(ms.contents.pt.x), int(ms.contents.pt.y), button)
                except Exception:
                    pass
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._hook_ready.clear()
        self._hook_installed = False
        cancel = threading.Event()
        self._callback_ref = HOOKPROC(proc)  # keep native callback alive

        def run():
            hook = None
            try:
                self._thread_id = kernel32.GetCurrentThreadId()
                msg = wt.MSG()
                # Create the thread message queue before another thread posts WM_QUIT.
                user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
                hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._callback_ref, None, 0)
                self._hook_installed = bool(hook)
                self._hook_ready.set()
                if not hook or cancel.is_set():
                    return
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                if hook:
                    user32.UnhookWindowsHookEx(hook)
                self._hook_ready.set()

        self._hook_thread = threading.Thread(target=run, daemon=True)
        self._hook_thread.start()
        if not self._hook_ready.wait(timeout=5):
            cancel.set()
            return False
        return self._hook_installed

    def stop_click_hook(self):
        if self._thread_id and user32:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._hook_thread:
            self._hook_thread.join(timeout=2.0)
            self._hook_thread = None
        self._callback_ref = None

    def describe(self):
        d = super().describe()
        d["geometry"] = self.screen_geometry()
        d["session"] = os.environ.get("USERNAME", "")
        return d
