"""board_window.py — 상황판 창: 트레이가 직접 만든 창 안에 WebView2(Edge 엔진)로 상황판 페이지를 그린다(Windows, 11-19).

전에는 트레이가 Edge 를 「앱 모드」(msedge --app)로 불러 창을 띄웠다. 그 창은 Edge 것이라
- 제목줄을 Edge 가 직접 그려 페이지 theme-color · DWM 색 · 실행 스위치가 안 먹었다(다크인데 제목줄만 밝음, 11-19 실측)
- 작업자가 보기엔 「툴」인데 실제로는 Edge 창이었다.
이제 창은 트레이 프로세스의 것이다. 제목줄 색은 DWM 속성(다크 모드 · 캡션 색 · 글자 색)으로 페이지 바탕색에 맞춘다.

필요한 것
- WebView2 런타임(Windows 11 기본 포함) + WebView2Loader.dll(Microsoft WebView2 SDK, 이 파일 옆 · 빌드가 넣음).
  하나라도 없으면 open() 이 False → 트레이가 예전처럼 Edge --app 으로 연다.
- 트레이의 창 메시지 루프가 도는 스레드에서 만들고 다룬다(WebView2 콜백이 그 루프로 온다).

COM 은 ctypes 로 직접 부른다(pythonnet · pywebview 없이). vtable 순서는 WebView2.h(SDK 1.0.x) 기준:
  ICoreWebView2Environment: 3 CreateCoreWebView2Controller
  ICoreWebView2Controller : 4 put_IsVisible · 6 put_Bounds · 7 get_ZoomFactor · 8 put_ZoomFactor · 9 add_ZoomFactorChanged · 12 MoveFocus
                            23 NotifyParentWindowPositionChanged · 24 Close · 25 get_CoreWebView2
  ICoreWebView2           : 5 Navigate · 7 add_NavigationStarting · 44 add_NewWindowRequested
  NavigationStartingEventArgs: 3 get_Uri · 8 put_Cancel   NewWindowRequestedEventArgs: 3 get_Uri · 6 put_Handled
"""
import ctypes
import ctypes.wintypes as W
import os
import time
from pathlib import Path

HRESULT = ctypes.c_long
LRESULT = ctypes.c_ssize_t
S_OK = 0
WS_OVERLAPPEDWINDOW = 0x00CF0000
CW_USEDEFAULT = -0x80000000
WM_SIZE, WM_MOVE, WM_CLOSE, WM_DESTROY, WM_SETFOCUS, WM_ACTIVATE = 0x0005, 0x0003, 0x0010, 0x0002, 0x0007, 0x0006
WM_DPICHANGED = 0x02E0
SW_SHOWNORMAL, SW_SHOWMAXIMIZED, SW_RESTORE = 1, 3, 9
DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 20, 35, 36

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")
dwmapi = ctypes.WinDLL("dwmapi")

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON), ("hCursor", W.HANDLE),
                ("hbrBackground", W.HANDLE), ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR), ("hIconSm", W.HICON)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", W.UINT), ("flags", W.UINT), ("showCmd", W.UINT), ("ptMinPosition", W.POINT),
                ("ptMaxPosition", W.POINT), ("rcNormalPosition", W.RECT)]


def _p(dll, name, res, *args):
    f = getattr(dll, name)
    f.restype, f.argtypes = res, list(args)
    return f


RegisterClassExW = _p(user32, "RegisterClassExW", W.ATOM, ctypes.POINTER(WNDCLASSEXW))
CreateWindowExW = _p(user32, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                     ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID)
DefWindowProcW = _p(user32, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
DestroyWindow = _p(user32, "DestroyWindow", W.BOOL, W.HWND)
ShowWindow = _p(user32, "ShowWindow", W.BOOL, W.HWND, ctypes.c_int)
UpdateWindow = _p(user32, "UpdateWindow", W.BOOL, W.HWND)
SetForegroundWindow = _p(user32, "SetForegroundWindow", W.BOOL, W.HWND)
IsIconic = _p(user32, "IsIconic", W.BOOL, W.HWND)
IsWindow = _p(user32, "IsWindow", W.BOOL, W.HWND)
GetClientRect = _p(user32, "GetClientRect", W.BOOL, W.HWND, ctypes.POINTER(W.RECT))
GetWindowPlacement = _p(user32, "GetWindowPlacement", W.BOOL, W.HWND, ctypes.POINTER(WINDOWPLACEMENT))
SetWindowPlacement = _p(user32, "SetWindowPlacement", W.BOOL, W.HWND, ctypes.POINTER(WINDOWPLACEMENT))
MonitorFromRect = _p(user32, "MonitorFromRect", W.HANDLE, ctypes.POINTER(W.RECT), W.DWORD)
LoadImageW = _p(user32, "LoadImageW", W.HANDLE, W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT)
LoadCursorW = _p(user32, "LoadCursorW", W.HANDLE, W.HINSTANCE, W.LPVOID)
GetModuleHandleW = _p(kernel32, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)
SetWindowPos = _p(user32, "SetWindowPos", W.BOOL, W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT)
DwmSetWindowAttribute = _p(dwmapi, "DwmSetWindowAttribute", HRESULT, W.HWND, W.DWORD, W.LPCVOID, W.DWORD)
CoInitializeEx = _p(ole32, "CoInitializeEx", HRESULT, W.LPVOID, W.DWORD)
CoTaskMemFree = _p(ole32, "CoTaskMemFree", None, W.LPVOID)

# ── COM 콜백 객체(IUnknown + Invoke 하나) ──
QI = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
REF = ctypes.WINFUNCTYPE(W.ULONG, ctypes.c_void_p)
DONE = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, HRESULT, ctypes.c_void_p)       # 완료 처리기: (this, 결과, 만든 것)
EVENT = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)  # 이벤트 처리기: (this, 보낸 쪽, 인자)


class _VTable(ctypes.Structure):
    _fields_ = [("QueryInterface", QI), ("AddRef", REF), ("Release", REF), ("Invoke", ctypes.c_void_p)]


class _Callback:
    """WebView2 가 부를 COM 객체. 파이썬 쪽이 붙들고 있는 동안 산다(참조 수는 세지 않음 — 창이 닫힐 때까지 이 객체를 들고 있다)."""

    def __init__(self, kind, fn):
        def qi(this, riid, ppv):
            ppv[0] = this
            return S_OK
        self._qi, self._ref = QI(qi), REF(lambda this: 1)
        self._inv = kind(fn)
        self._vt = _VTable(self._qi, self._ref, self._ref, ctypes.cast(self._inv, ctypes.c_void_p))
        self._obj = (ctypes.c_void_p * 1)(ctypes.addressof(self._vt))  # COM 객체 = vtable 포인터를 담은 메모리
        self.ptr = ctypes.addressof(self._obj)


def _method(obj, index, *argtypes):
    """COM 객체 obj(포인터 값)의 vtable[index] 를 부를 함수."""
    vtbl = ctypes.cast(ctypes.c_void_p(obj), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
    return lambda *a: fn(obj, *a)


def _addref(obj):
    vtbl = ctypes.cast(ctypes.c_void_p(obj), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    ctypes.WINFUNCTYPE(W.ULONG, ctypes.c_void_p)(vtbl[1])(obj)


def _release(obj):
    if obj:
        vtbl = ctypes.cast(ctypes.c_void_p(obj), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(W.ULONG, ctypes.c_void_p)(vtbl[2])(obj)


def _take_str(fn):
    """LPWSTR* 를 돌려주는 getter 를 불러 문자열로(메모리는 CoTaskMemFree)."""
    p = ctypes.c_void_p()
    if fn(ctypes.byref(p)) != S_OK or not p.value:
        return ""
    try:
        return ctypes.wstring_at(p.value)
    finally:
        CoTaskMemFree(p)


def find_loader(extra=None):
    """WebView2Loader.dll 위치: 인자 · 환경변수 SO_WEBVIEW2_LOADER · 이 파일 옆. 없으면 None."""
    for c in (extra, os.environ.get("SO_WEBVIEW2_LOADER"), Path(__file__).with_name("WebView2Loader.dll")):
        if c and Path(c).is_file():
            return str(c)
    return None


def _rgb(s):
    """'rgb(28, 27, 34)' → COLORREF(0x00BBGGRR). 못 읽으면 None."""
    try:
        r, g, b = [int(float(x)) for x in str(s).strip()[str(s).index("(") + 1:str(s).index(")")].split(",")[:3]]
        return (b << 16) | (g << 8) | r
    except (ValueError, IndexError):
        return None


class BoardWindow:
    """상황판 창 하나. 트레이 UI 스레드에서만 부른다."""

    CLASS = "SessionOrchestratorBoard"

    def __init__(self, title, icon=None, data_dir=None, local_prefix="", open_external=None, on_closed=None, log=print, loader=None,
                 zoom=None, on_zoom=None):
        self.title = title
        self.icon = str(icon) if icon else None
        self.data_dir = str(data_dir) if data_dir else None
        self.local_prefix = local_prefix          # 이 주소로 시작하는 곳만 창 안에서 연다(나머지는 기본 브라우저 · 앱으로)
        self.open_external = open_external or (lambda u: os.startfile(u))
        self.on_closed = on_closed                # 창을 닫을 때 placement dict 를 넘긴다(트레이가 설정에 저장)
        # 배율(확대/축소, 1.0 = 100%). Ctrl+휠 · Ctrl+−/+ 로 바꾸면 on_zoom 으로 알려 트레이가 기억한다(11-19: 예전 Edge 창은 90% 였는데
        # 새 창이 100% 로 열려 글자가 커 보였다 — WebView2 배율은 창을 닫으면 사라지므로 직접 기억해 다시 건다)
        self.zoom = zoom
        self.on_zoom = on_zoom
        self.t_open = 0.0
        self.log = log
        self.loader = find_loader(loader)
        self.hwnd = None
        self.env = self.ctrl = self.wv = None
        self.pending_url = None
        self.caption = None                       # (bg, fg, dark) — 창이 생기면 적용
        self._keep = []                           # 콜백 객체(GC 방지)
        self._wndproc = WNDPROC(self._proc)
        self._registered = False

    # ── 바깥에서 부르는 것 ──
    def available(self) -> bool:
        return bool(self.loader)

    def alive(self) -> bool:
        return bool(self.hwnd) and bool(IsWindow(self.hwnd))

    def open(self, url, placement=None) -> bool:
        """창을 만들고 url 로 연다. WebView2 를 못 쓰면 False(창도 안 만든다)."""
        if self.alive():
            self.navigate(url)
            self.show()
            return True
        if not self.loader:
            return False
        try:
            dll = ctypes.WinDLL(self.loader)
            create = dll.CreateCoreWebView2EnvironmentWithOptions
            create.restype, create.argtypes = HRESULT, [W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
        except (OSError, AttributeError) as e:
            self.log("board-window: 로더를 못 읽음:", e)
            return False
        CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED(이미 했으면 S_FALSE)
        self.t_open = time.time()
        self._create_window(placement)
        self.pending_url = url
        done = _Callback(DONE, self._env_ready)
        self._keep.append(done)
        if self.data_dir:
            Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        hr = create(None, self.data_dir, None, done.ptr)
        if hr != S_OK:
            self.log(f"board-window: WebView2 환경을 못 만듦 hr=0x{hr & 0xFFFFFFFF:08x}")
            self._destroy()
            return False
        return True

    def show(self):
        if not self.alive():
            return
        if IsIconic(self.hwnd):
            ShowWindow(self.hwnd, SW_RESTORE)
        SetForegroundWindow(self.hwnd)

    def navigate(self, url):
        if self.wv:
            _method(self.wv, 5, W.LPCWSTR)(url)
        else:
            self.pending_url = url

    def set_caption(self, bg=None, fg=None, dark=None):
        """제목줄을 페이지 바탕색에 맞춘다(Windows 11: 캡션 색 · 글자 색, Windows 10: 다크 모드만)."""
        self.caption = (bg, fg, dark)
        if not self.alive():
            return
        if dark is not None:
            v = W.BOOL(1 if dark else 0)
            DwmSetWindowAttribute(self.hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(v), 4)
        for attr, s in ((DWMWA_CAPTION_COLOR, bg), (DWMWA_TEXT_COLOR, fg)):
            c = _rgb(s) if s else None
            if c is not None:
                v = W.DWORD(c)
                DwmSetWindowAttribute(self.hwnd, attr, ctypes.byref(v), 4)

    def close(self):
        if self.alive():
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    # ── 안쪽 ──
    def _create_window(self, placement):
        hinst = GetModuleHandleW(None)
        if not self._registered:
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.hCursor = LoadCursorW(None, ctypes.c_void_p(32512))  # IDC_ARROW
            wc.lpszClassName = self.CLASS
            if self.icon and Path(self.icon).is_file():
                wc.hIcon = LoadImageW(None, self.icon, 1, 0, 0, 0x0010 | 0x0040)      # IMAGE_ICON, LR_LOADFROMFILE | LR_DEFAULTSIZE
                wc.hIconSm = LoadImageW(None, self.icon, 1, 16, 16, 0x0010)
            RegisterClassExW(ctypes.byref(wc))
            self._registered = True
        self.hwnd = CreateWindowExW(0, self.CLASS, self.title, WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 1400, 900,
                                    None, None, hinst, None)
        show = SW_SHOWNORMAL
        if placement:
            try:
                x, y, w, h = [int(v) for v in placement.get("rect", [])[:4]]
                r = W.RECT(x, y, x + w, y + h)
                if w > 200 and h > 150 and MonitorFromRect(ctypes.byref(r), 0):  # MONITOR_DEFAULTTONULL — 빠진 모니터면 기본 위치
                    wp = WINDOWPLACEMENT()
                    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
                    wp.showCmd = SW_SHOWNORMAL
                    wp.rcNormalPosition = r
                    SetWindowPlacement(self.hwnd, ctypes.byref(wp))
                if placement.get("max"):
                    show = SW_SHOWMAXIMIZED
            except (TypeError, ValueError):
                pass
        if self.caption:
            self.set_caption(*self.caption)
        ShowWindow(self.hwnd, show)
        UpdateWindow(self.hwnd)
        SetForegroundWindow(self.hwnd)

    def _placement(self):
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not GetWindowPlacement(self.hwnd, ctypes.byref(wp)):
            return None
        r = wp.rcNormalPosition
        return {"rect": [r.left, r.top, r.right - r.left, r.bottom - r.top], "max": wp.showCmd == SW_SHOWMAXIMIZED}

    def _fit(self):
        if self.ctrl and self.alive():
            r = W.RECT()
            GetClientRect(self.hwnd, ctypes.byref(r))
            _method(self.ctrl, 6, W.RECT)(r)

    def _env_ready(self, this, hr, env):
        try:
            if hr != S_OK or not env or not self.alive():
                self.log(f"board-window: 환경 실패 hr=0x{hr & 0xFFFFFFFF:08x}")
                return S_OK
            _addref(env)
            self.env = env
            done = _Callback(DONE, self._ctrl_ready)
            self._keep.append(done)
            _method(env, 3, W.HWND, ctypes.c_void_p)(self.hwnd, done.ptr)
        except Exception as e:
            self.log("board-window env:", repr(e))
        return S_OK

    def _ctrl_ready(self, this, hr, ctrl):
        try:
            if hr != S_OK or not ctrl or not self.alive():
                self.log(f"board-window: 컨트롤러 실패 hr=0x{hr & 0xFFFFFFFF:08x} · {time.time() - self.t_open:.1f}초 · 창 " + ("있음" if self.alive() else "닫힘(만드는 도중 닫으면 0x80004004)"))
                return S_OK
            _addref(ctrl)
            self.ctrl = ctrl
            wv = ctypes.c_void_p()
            _method(ctrl, 25, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(wv))
            self.wv = wv.value
            tok = ctypes.c_int64()
            nav = _Callback(EVENT, self._on_navigating)
            neww = _Callback(EVENT, self._on_new_window)
            self._keep += [nav, neww]
            _method(self.wv, 7, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64))(nav.ptr, ctypes.byref(tok))
            _method(self.wv, 44, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64))(neww.ptr, ctypes.byref(tok))
            self._fit()
            if self.zoom and 0.25 <= float(self.zoom) <= 5 and abs(float(self.zoom) - 1.0) > 0.001:
                _method(ctrl, 8, ctypes.c_double)(float(self.zoom))  # put_ZoomFactor
            zc = _Callback(EVENT, self._on_zoom)
            self._keep.append(zc)
            _method(ctrl, 9, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64))(zc.ptr, ctypes.byref(tok))  # add_ZoomFactorChanged
            _method(ctrl, 4, W.BOOL)(1)
            _method(ctrl, 12, ctypes.c_int)(0)  # MoveFocus(PROGRAMMATIC)
            if self.pending_url:
                self.navigate(self.pending_url)
                self.pending_url = None
            self.log(f"board-window: 열림 {time.time() - self.t_open:.1f}초" + (f" · 배율 {round(float(self.zoom) * 100)}%" if self.zoom else ""))
        except Exception as e:
            self.log("board-window ctrl:", repr(e))
        return S_OK

    def _on_zoom(self, this, sender, args):
        try:
            z = ctypes.c_double()
            if self.ctrl and _method(self.ctrl, 7, ctypes.POINTER(ctypes.c_double))(ctypes.byref(z)) == S_OK:
                self.zoom = z.value
                if self.on_zoom:
                    self.on_zoom(z.value)
        except Exception as e:
            self.log("board-window zoom:", repr(e))
        return S_OK

    def _local(self, uri):
        return not self.local_prefix or uri.startswith(self.local_prefix) or uri.startswith("about:") or uri.startswith("data:")

    def _on_navigating(self, this, sender, args):
        """상황판 밖으로 가는 이동(링크 등)은 막고 기본 브라우저 · 해당 앱으로 연다."""
        try:
            uri = _take_str(_method(args, 3, ctypes.POINTER(ctypes.c_void_p)))
            if uri and not self._local(uri):
                _method(args, 8, W.BOOL)(1)  # put_Cancel
                self._external(uri)
        except Exception as e:
            self.log("board-window nav:", repr(e))
        return S_OK

    def _on_new_window(self, this, sender, args):
        """새 창(target=_blank · window.open)은 창 안에 안 열고 밖으로."""
        try:
            uri = _take_str(_method(args, 3, ctypes.POINTER(ctypes.c_void_p)))
            _method(args, 6, W.BOOL)(1)  # put_Handled
            if uri:
                self._external(uri)
        except Exception as e:
            self.log("board-window new:", repr(e))
        return S_OK

    def _external(self, uri):
        if uri.split(":", 1)[0].lower() in ("http", "https", "claude", "mailto"):
            try:
                self.open_external(uri)
            except OSError as e:
                self.log("board-window open:", e)

    def _destroy(self):
        if self.ctrl:
            try:
                _method(self.ctrl, 24)()  # Close
            except Exception:
                pass
        for o in (self.wv, self.ctrl, self.env):
            try:
                _release(o)
            except Exception:
                pass
        self.wv = self.ctrl = self.env = None
        if self.hwnd and IsWindow(self.hwnd):
            DestroyWindow(self.hwnd)
        self.hwnd = None
        self._keep.clear()

    def _proc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_SIZE:
                self._fit()
                return 0
            if msg == WM_DPICHANGED:  # 다른 배율 모니터로 옮기면 Windows 가 권하는 크기로
                r = ctypes.cast(lp, ctypes.POINTER(W.RECT)).contents
                SetWindowPos(hwnd, None, r.left, r.top, r.right - r.left, r.bottom - r.top, 0x0014)  # SWP_NOZORDER | SWP_NOACTIVATE
                return 0
            if msg == WM_MOVE:
                if self.ctrl:
                    _method(self.ctrl, 23)()
                return 0
            if msg in (WM_SETFOCUS, WM_ACTIVATE):
                if self.ctrl and (msg == WM_SETFOCUS or (wp & 0xFFFF)):
                    _method(self.ctrl, 12, ctypes.c_int)(0)
                if msg == WM_SETFOCUS:
                    return 0
            if msg == WM_CLOSE:
                pl = self._placement() if self.hwnd else None
                self._destroy()
                if self.on_closed and pl:
                    try:
                        self.on_closed(pl)
                    except Exception as e:
                        self.log("board-window closed:", repr(e))
                return 0
        except Exception as e:
            self.log("board-window proc:", repr(e))
        return DefWindowProcW(hwnd, msg, wp, lp)
