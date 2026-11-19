"""board_window.py — 상황판 창: 트레이가 직접 만든 창 안에 WebView2(Edge 엔진)로 상황판 페이지를 그린다(Windows, 11-19).

전에는 트레이가 Edge 를 「앱 모드」(msedge --app)로 불러 창을 띄웠다. 그 창은 Edge 것이라
- 제목줄을 Edge 가 직접 그려 페이지 theme-color · DWM 색 · 실행 스위치가 안 먹었다(다크인데 제목줄만 밝음, 11-19 실측)
- 작업자가 보기엔 「툴」인데 실제로는 Edge 창이었다.
이제 창은 트레이 프로세스의 것이다.

맨 윗줄(v1.4.11): Windows 제목줄을 쓰지 않고 이 창이 직접 그린다(로고 · 이름 · 최소화 · 최대화 · 닫기).
- Windows 제목줄은 페이지 배율을 따라가지 않아, 페이지를 90% 로 보면 제목만 커 보였다(11-19 작업자). 같은 WebView2 앱인 사내 도구가
  제목줄 없이 맨 윗줄을 직접 그리는 방식이라 그쪽을 따랐다. 맨 윗줄은 배율 · 모니터 배율을 따라 높이 · 글자가 같이 바뀌고, 색은 페이지 맨 위 바탕색.
- WM_NCCALCSIZE 로 제목줄 자리만 클라이언트로 돌려받고(왼 · 오른 · 아래 크기 조절 테두리는 Windows 그대로), 맨 윗줄은 WM_NCHITTEST 로
  HTCAPTION · HTMINBUTTON · HTMAXBUTTON · HTCLOSE 를 돌려준다 → 끌어 옮기기 · 가장자리 붙이기 · 두 번 눌러 최대화 · 시스템 메뉴 ·
  (Windows 11) 최대화 버튼 위 맞춤 레이아웃이 Windows 동작 그대로. WebView2 는 맨 윗줄 아래에만 놓는다.

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
WM_PAINT, WM_ERASEBKGND, WM_TIMER, WM_DPICHANGED = 0x000F, 0x0014, 0x0113, 0x02E0
WM_NCCALCSIZE, WM_NCHITTEST, WM_NCACTIVATE = 0x0083, 0x0084, 0x0086
WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP, WM_NCMOUSELEAVE = 0x00A0, 0x00A1, 0x00A2, 0x02A2
HTCLIENT, HTCAPTION, HTMINBUTTON, HTMAXBUTTON, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTCLOSE = 1, 2, 8, 9, 12, 13, 14, 20
SW_SHOWNORMAL, SW_SHOWMAXIMIZED, SW_MINIMIZE, SW_RESTORE = 1, 3, 6, 9
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
RETRY_TIMER, ZOOM_TIMER = 0x5301, 0x5302

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
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


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", W.RECT * 3), ("lppos", ctypes.c_void_p)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", W.HDC), ("fErase", W.BOOL), ("rcPaint", W.RECT), ("fRestore", W.BOOL), ("fIncUpdate", W.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("dwFlags", W.DWORD), ("hwndTrack", W.HWND), ("dwHoverTime", W.DWORD)]


def _p(dll, name, res, *args):
    f = getattr(dll, name)
    f.restype, f.argtypes = res, list(args)
    return f


H = W.HANDLE
RegisterClassExW = _p(user32, "RegisterClassExW", W.ATOM, ctypes.POINTER(WNDCLASSEXW))
CreateWindowExW = _p(user32, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                     ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID)
DefWindowProcW = _p(user32, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
DestroyWindow = _p(user32, "DestroyWindow", W.BOOL, W.HWND)
ShowWindow = _p(user32, "ShowWindow", W.BOOL, W.HWND, ctypes.c_int)
UpdateWindow = _p(user32, "UpdateWindow", W.BOOL, W.HWND)
SetForegroundWindow = _p(user32, "SetForegroundWindow", W.BOOL, W.HWND)
IsIconic = _p(user32, "IsIconic", W.BOOL, W.HWND)
IsZoomed = _p(user32, "IsZoomed", W.BOOL, W.HWND)
IsWindow = _p(user32, "IsWindow", W.BOOL, W.HWND)
GetClientRect = _p(user32, "GetClientRect", W.BOOL, W.HWND, ctypes.POINTER(W.RECT))
ScreenToClient = _p(user32, "ScreenToClient", W.BOOL, W.HWND, ctypes.POINTER(W.POINT))
GetWindowPlacement = _p(user32, "GetWindowPlacement", W.BOOL, W.HWND, ctypes.POINTER(WINDOWPLACEMENT))
SetWindowPlacement = _p(user32, "SetWindowPlacement", W.BOOL, W.HWND, ctypes.POINTER(WINDOWPLACEMENT))
MonitorFromRect = _p(user32, "MonitorFromRect", H, ctypes.POINTER(W.RECT), W.DWORD)
LoadImageW = _p(user32, "LoadImageW", H, W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT)
LoadCursorW = _p(user32, "LoadCursorW", H, W.HINSTANCE, W.LPVOID)
SetWindowPos = _p(user32, "SetWindowPos", W.BOOL, W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT)
InvalidateRect = _p(user32, "InvalidateRect", W.BOOL, W.HWND, ctypes.POINTER(W.RECT), W.BOOL)
BeginPaint = _p(user32, "BeginPaint", H, W.HWND, ctypes.POINTER(PAINTSTRUCT))
EndPaint = _p(user32, "EndPaint", W.BOOL, W.HWND, ctypes.POINTER(PAINTSTRUCT))
FillRect = _p(user32, "FillRect", ctypes.c_int, H, ctypes.POINTER(W.RECT), H)
DrawTextW = _p(user32, "DrawTextW", ctypes.c_int, H, W.LPCWSTR, ctypes.c_int, ctypes.POINTER(W.RECT), W.UINT)
DrawIconEx = _p(user32, "DrawIconEx", W.BOOL, H, ctypes.c_int, ctypes.c_int, H, ctypes.c_int, ctypes.c_int, W.UINT, H, W.UINT)
DestroyIcon = _p(user32, "DestroyIcon", W.BOOL, H)
TrackMouseEvent = _p(user32, "TrackMouseEvent", W.BOOL, ctypes.POINTER(TRACKMOUSEEVENT))
SetTimer = _p(user32, "SetTimer", ctypes.c_size_t, W.HWND, ctypes.c_size_t, W.UINT, ctypes.c_void_p)
KillTimer = _p(user32, "KillTimer", W.BOOL, W.HWND, ctypes.c_size_t)
PostMessageW = _p(user32, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
try:
    GetDpiForWindow = _p(user32, "GetDpiForWindow", W.UINT, W.HWND)
    GetSystemMetricsForDpi = _p(user32, "GetSystemMetricsForDpi", ctypes.c_int, ctypes.c_int, W.UINT)
except AttributeError:  # Windows 10 1607 이전
    GetDpiForWindow = lambda h: 96
    GetSystemMetricsForDpi = lambda i, d: user32.GetSystemMetrics(i)
CreateCompatibleDC = _p(gdi32, "CreateCompatibleDC", H, H)
CreateCompatibleBitmap = _p(gdi32, "CreateCompatibleBitmap", H, H, ctypes.c_int, ctypes.c_int)
SelectObject = _p(gdi32, "SelectObject", H, H, H)
DeleteObject = _p(gdi32, "DeleteObject", W.BOOL, H)
DeleteDC = _p(gdi32, "DeleteDC", W.BOOL, H)
BitBlt = _p(gdi32, "BitBlt", W.BOOL, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, ctypes.c_int, ctypes.c_int, W.DWORD)
CreateSolidBrush = _p(gdi32, "CreateSolidBrush", H, W.DWORD)
CreateFontW = _p(gdi32, "CreateFontW", H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.DWORD, W.DWORD,
                 W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR)
SetBkMode = _p(gdi32, "SetBkMode", ctypes.c_int, H, ctypes.c_int)
SetTextColor = _p(gdi32, "SetTextColor", W.DWORD, H, W.DWORD)
GetTextFaceW = _p(gdi32, "GetTextFaceW", ctypes.c_int, H, ctypes.c_int, W.LPWSTR)
GetModuleHandleW = _p(kernel32, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)
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


def _rgb3(s):
    """'rgb(28, 27, 34)' → (28, 27, 34). 못 읽으면 None."""
    try:
        s = str(s)
        r, g, b = [int(float(x)) for x in s[s.index("(") + 1:s.index(")")].split(",")[:3]]
        return (r, g, b)
    except (ValueError, IndexError):
        return None


def _cref(c):
    return (c[2] << 16) | (c[1] << 8) | c[0]


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


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
        # 새 창이 100% 로 열려 글자가 커 보였다 — WebView2 배율은 창을 닫으면 사라지므로 직접 기억해 다시 건다). 맨 윗줄도 이 배율을 따른다
        self.zoom = zoom
        self.on_zoom = on_zoom
        self.t_open = 0.0
        self.log = log
        self.loader = find_loader(loader)
        self.hwnd = None
        self.env = self.ctrl = self.wv = None     # env 는 창을 닫아도 들고 있다가 다음 창에 다시 쓴다(아래 _destroy)
        self._create = None                       # CreateCoreWebView2EnvironmentWithOptions
        self.retries = 0
        self.pending_url = None
        self.bg, self.fg, self.dark = (32, 32, 32), (240, 240, 240), True  # 맨 윗줄 색(페이지가 올린 맨 위 바탕색 · 글자색)
        self.active = True
        self.hover = self.pressed = None          # 맨 윗줄 버튼(HTMINBUTTON · HTMAXBUTTON · HTCLOSE)
        self._tracking = False
        self._icons = {}                          # 크기 → HICON
        self._glyph_face = None
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
        if self._create is None:
            try:
                dll = ctypes.WinDLL(self.loader)
                self._create = dll.CreateCoreWebView2EnvironmentWithOptions
                self._create.restype, self._create.argtypes = HRESULT, [W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
            except (OSError, AttributeError) as e:
                self.log("board-window: 로더를 못 읽음:", e)
                return False
        CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED(이미 했으면 S_FALSE)
        self.t_open = time.time()
        self.retries = 0
        self._create_window(placement)
        self.pending_url = url
        if self.env:  # 앞 창에서 쓰던 환경을 그대로(닫자마자 다시 열 때 사라지는 중인 브라우저에 붙어 실패하던 것, 11-19)
            self._make_controller()
            return True
        if not self._make_env():
            self._destroy(keep_env=False)
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
        """맨 윗줄 색을 페이지 맨 위 바탕색 · 글자색에 맞춘다. dark 는 창 테두리 · 그림자(DWM 다크 모드)."""
        b, f = _rgb3(bg) if bg else None, _rgb3(fg) if fg else None
        if dark is not None:
            self.dark = bool(dark)
        if b:
            self.bg = b
        elif dark is not None:
            self.bg = (32, 32, 32) if self.dark else (243, 243, 243)
        if f:
            self.fg = f
        elif dark is not None:
            self.fg = (240, 240, 240) if self.dark else (26, 26, 26)
        if self.alive():
            v = W.BOOL(1 if self.dark else 0)
            DwmSetWindowAttribute(self.hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(v), 4)
            self._repaint()

    def close(self):
        if self.alive():
            PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    # ── 맨 윗줄 ──
    def _metrics(self):
        """맨 윗줄 치수(물리 픽셀): 모니터 배율 × 페이지 배율."""
        dpi = GetDpiForWindow(self.hwnd) if self.hwnd else 96
        s = (dpi or 96) / 96.0
        z = float(self.zoom or 1.0)
        z = min(max(z, 0.5), 2.0)
        return {
            "s": s, "dpi": dpi or 96,
            "bar": max(round(32 * s * z), round(24 * s)),   # 맨 윗줄 높이
            "btn": max(round(46 * s * z), round(34 * s)),   # 버튼 너비(Windows 기본 46)
            "icon": round(16 * s * z), "pad": round(12 * s * z), "gap": round(8 * s * z),
            "font": max(round(12.5 * s * z), round(10 * s)), "glyph": max(round(10 * s * z), round(8 * s)),
            "grip": round(5 * s),                            # 위쪽 크기 조절 손잡이(최대화 아닐 때)
        }

    def _client(self):
        r = W.RECT()
        GetClientRect(self.hwnd, ctypes.byref(r))
        return r.right, r.bottom

    def _repaint(self, bar_only=True):
        if self.alive():
            if bar_only:
                cw, _ = self._client()
                r = W.RECT(0, 0, cw, self._metrics()["bar"])
                InvalidateRect(self.hwnd, ctypes.byref(r), False)
            else:
                InvalidateRect(self.hwnd, None, False)

    def _icon(self, size):
        if size not in self._icons and self.icon and Path(self.icon).is_file():
            self._icons[size] = LoadImageW(None, self.icon, 1, size, size, 0x0010)  # IMAGE_ICON, LR_LOADFROMFILE
        return self._icons.get(size)

    def _glyph_font(self, hdc, px):
        """창 버튼 글리프 글꼴: Segoe Fluent Icons(Windows 11) → 없으면 Segoe MDL2 Assets(Windows 10)."""
        for face in ([self._glyph_face] if self._glyph_face else ["Segoe Fluent Icons", "Segoe MDL2 Assets"]):
            f = CreateFontW(-px, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, face)
            old = SelectObject(hdc, f)
            buf = ctypes.create_unicode_buffer(64)
            GetTextFaceW(hdc, 64, buf)
            SelectObject(hdc, old)
            if buf.value.lower() == face.lower() or self._glyph_face:
                self._glyph_face = face
                return f
            DeleteObject(f)
        return CreateFontW(-px, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")

    def _paint(self, hwnd):
        ps = PAINTSTRUCT()
        hdc = BeginPaint(hwnd, ctypes.byref(ps))
        try:
            m = self._metrics()
            cw, ch = self._client()
            bar = m["bar"]
            brush = CreateSolidBrush(_cref(self.bg))
            # 맨 윗줄 아래(WebView2 가 붙기 전 · 테두리 틈)도 같은 색으로
            if ch > bar:
                FillRect(hdc, ctypes.byref(W.RECT(0, bar, cw, ch)), brush)
            mdc = CreateCompatibleDC(hdc)
            bmp = CreateCompatibleBitmap(hdc, max(cw, 1), max(bar, 1))
            oldb = SelectObject(mdc, bmp)
            FillRect(mdc, ctypes.byref(W.RECT(0, 0, cw, bar)), brush)
            DeleteObject(brush)
            SetBkMode(mdc, 1)  # TRANSPARENT
            fg = self.fg if self.active else _mix(self.fg, self.bg, 0.45)
            x = m["pad"]
            ic = self._icon(m["icon"])
            if ic:
                DrawIconEx(mdc, x, (bar - m["icon"]) // 2, ic, m["icon"], m["icon"], 0, None, 3)  # DI_NORMAL
                x += m["icon"] + m["gap"]
            font = CreateFontW(-m["font"], 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
            oldf = SelectObject(mdc, font)
            SetTextColor(mdc, _cref(fg))
            DrawTextW(mdc, self.title, -1, ctypes.byref(W.RECT(x, 0, max(x, cw - 3 * m["btn"] - m["gap"]), bar)),
                      0x0000 | 0x0004 | 0x0020 | 0x8000 | 0x0800)  # LEFT · VCENTER · SINGLELINE · END_ELLIPSIS · NOPREFIX
            SelectObject(mdc, oldf)
            DeleteObject(font)
            gfont = self._glyph_font(mdc, m["glyph"])
            oldf = SelectObject(mdc, gfont)
            maxed = bool(IsZoomed(hwnd))
            for i, (code, glyph) in enumerate(((HTMINBUTTON, ""), (HTMAXBUTTON, "" if maxed else ""), (HTCLOSE, ""))):
                x0 = cw - (3 - i) * m["btn"]
                r = W.RECT(x0, 0, x0 + m["btn"], bar)
                col = fg
                if self.hover == code or self.pressed == code:
                    if code == HTCLOSE:
                        hb, col = ((232, 17, 35) if self.pressed != code else (241, 112, 122)), (255, 255, 255)
                    else:
                        hb = _mix(self.bg, self.fg, 0.10 if self.pressed != code else 0.18)
                    b2 = CreateSolidBrush(_cref(hb))
                    FillRect(mdc, ctypes.byref(r), b2)
                    DeleteObject(b2)
                SetTextColor(mdc, _cref(col))
                DrawTextW(mdc, glyph, -1, ctypes.byref(r), 0x0001 | 0x0004 | 0x0020)  # CENTER · VCENTER · SINGLELINE
            SelectObject(mdc, oldf)
            DeleteObject(gfont)
            BitBlt(hdc, 0, 0, cw, bar, mdc, 0, 0, 0x00CC0020)  # SRCCOPY
            SelectObject(mdc, oldb)
            DeleteObject(bmp)
            DeleteDC(mdc)
        finally:
            EndPaint(hwnd, ctypes.byref(ps))

    def _hit(self, hwnd, lp):
        pt = W.POINT(ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value)
        ScreenToClient(hwnd, ctypes.byref(pt))
        m = self._metrics()
        cw, _ = self._client()
        if pt.y < 0 or pt.y >= m["bar"]:
            return HTCLIENT
        if not IsZoomed(hwnd) and pt.y < m["grip"]:
            return HTTOPLEFT if pt.x < m["grip"] * 2 else HTTOPRIGHT if pt.x >= cw - m["grip"] * 2 else HTTOP
        if pt.x >= cw - 3 * m["btn"]:
            return (HTMINBUTTON, HTMAXBUTTON, HTCLOSE)[min(2, max(0, (pt.x - (cw - 3 * m["btn"])) // m["btn"]))]
        return HTCAPTION

    # ── 창 ──
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
        self.hover = self.pressed = None
        self.hwnd = CreateWindowExW(0, self.CLASS, self.title, WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 1400, 900,
                                    None, None, hinst, None)
        SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)  # 테두리 다시 계산(SWP_FRAMECHANGED)
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
        v = W.BOOL(1 if self.dark else 0)
        DwmSetWindowAttribute(self.hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(v), 4)
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
        """WebView2 는 맨 윗줄 아래에만."""
        if self.ctrl and self.alive():
            cw, ch = self._client()
            bar = self._metrics()["bar"]
            _method(self.ctrl, 6, W.RECT)(W.RECT(0, min(bar, ch), cw, ch))

    # ── WebView2 ──
    def _make_env(self) -> bool:
        done = _Callback(DONE, self._env_ready)
        self._keep.append(done)
        if self.data_dir:
            Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        hr = self._create(None, self.data_dir, None, done.ptr)
        if hr != S_OK:
            self.log(f"board-window: WebView2 환경을 못 만듦 hr=0x{hr & 0xFFFFFFFF:08x}")
            return False
        return True

    def _make_controller(self):
        done = _Callback(DONE, self._ctrl_ready)
        self._keep.append(done)
        hr = _method(self.env, 3, W.HWND, ctypes.c_void_p)(self.hwnd, done.ptr)
        if hr != S_OK:
            self._retry(f"컨트롤러 요청 실패 hr=0x{hr & 0xFFFFFFFF:08x}")

    def _retry(self, why):
        """환경을 새로 만들어 한 번 더(사라지는 중인 브라우저 프로세스에 붙었거나 브라우저가 죽은 경우). 두 번까지."""
        self.log(f"board-window: {why} · {time.time() - self.t_open:.1f}초 · 창 " + ("있음" if self.alive() else "닫힘"))
        _release(self.env)
        self.env = None
        if self.alive() and self.retries < 2:
            self.retries += 1
            SetTimer(self.hwnd, RETRY_TIMER, 1200, None)

    def _env_ready(self, this, hr, env):
        try:
            if hr != S_OK or not env:
                self._retry(f"환경 실패 hr=0x{hr & 0xFFFFFFFF:08x}")
                return S_OK
            _addref(env)
            self.env = env
            if self.alive():
                self._make_controller()
        except Exception as e:
            self.log("board-window env:", repr(e))
        return S_OK

    def _ctrl_ready(self, this, hr, ctrl):
        try:
            if not self.alive():
                if hr == S_OK and ctrl:
                    _addref(ctrl)
                    _method(ctrl, 24)()
                    _release(ctrl)
                return S_OK
            if hr != S_OK or not ctrl:
                self._retry(f"컨트롤러 실패 hr=0x{hr & 0xFFFFFFFF:08x}")
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
            # 배율 바뀜 알림(ZoomFactorChanged)이 실제로 안 와서(11-19 실측: 구독 S_OK · 배율은 바뀌는데 호출 0번) 열려 있는 동안 읽어서 본다
            SetTimer(self.hwnd, ZOOM_TIMER, 700, None)
            _method(ctrl, 4, W.BOOL)(1)
            _method(ctrl, 12, ctypes.c_int)(0)  # MoveFocus(PROGRAMMATIC)
            if self.pending_url:
                self.navigate(self.pending_url)
                self.pending_url = None
            self.log(f"board-window: 열림 {time.time() - self.t_open:.1f}초" + (f" · 배율 {round(float(self.zoom) * 100)}%" if self.zoom else "")
                     + (f" · 다시 시도 {self.retries}번" if self.retries else ""))
        except Exception as e:
            self.log("board-window ctrl:", repr(e))
        return S_OK

    def _on_zoom(self, this=None, sender=None, args=None):
        """배율이 바뀌었으면 맨 윗줄 높이 · WebView2 자리를 다시 잡고 트레이에 알린다(이벤트 · 타이머 둘 다 여기로)."""
        try:
            z = ctypes.c_double()
            if self.ctrl and _method(self.ctrl, 7, ctypes.POINTER(ctypes.c_double))(ctypes.byref(z)) == S_OK:
                if self.zoom is not None and abs(float(self.zoom) - z.value) < 0.001:
                    return S_OK
                self.zoom = z.value
                self._fit()          # 맨 윗줄 높이가 배율을 따라 바뀐다
                self._repaint(False)
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

    def _destroy(self, keep_env=True):
        """창을 닫는다. WebView2 환경(브라우저 프로세스)은 들고 있다가 다음 창에 쓴다 — 닫고 곧바로 다시 열면 끝나는 중인
        브라우저에 새 환경이 붙어 컨트롤러가 0x80004004(E_ABORT)로 실패했다(11-19 실측 2번)."""
        if self.ctrl:
            try:
                _method(self.ctrl, 24)()  # Close
            except Exception:
                pass
        for o in (self.wv, self.ctrl) + (() if keep_env else (self.env,)):
            try:
                _release(o)
            except Exception:
                pass
        self.wv = self.ctrl = None
        if not keep_env:
            self.env = None
        if self.hwnd and IsWindow(self.hwnd):
            KillTimer(self.hwnd, RETRY_TIMER)
            KillTimer(self.hwnd, ZOOM_TIMER)
            DestroyWindow(self.hwnd)
        self.hwnd = None
        # 콜백 객체는 비우지 않는다: 만드는 도중 닫으면 WebView2 가 나중에 이 콜백을 부른다(풀려 있으면 튕김). 최근 것만 남긴다
        self._keep = self._keep[-30:]

    def _proc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_NCCALCSIZE and wp:
                # 제목줄 자리만 클라이언트로(왼 · 오른 · 아래 테두리는 Windows 기본 — 크기 조절 그대로). 최대화면 화면 밖으로
                # 나간 위쪽 테두리 두께만큼 내린다(안 그러면 맨 윗줄이 잘림)
                p = ctypes.cast(lp, ctypes.POINTER(NCCALCSIZE_PARAMS)).contents
                top = p.rgrc[0].top
                DefWindowProcW(hwnd, msg, wp, lp)
                p.rgrc[0].top = top
                if IsZoomed(hwnd):
                    dpi = GetDpiForWindow(hwnd) or 96
                    p.rgrc[0].top += GetSystemMetricsForDpi(33, dpi) + GetSystemMetricsForDpi(92, dpi)  # SM_CYFRAME + SM_CXPADDEDBORDER
                return 0
            if msg == WM_NCHITTEST:
                hit = DefWindowProcW(hwnd, msg, wp, lp)
                return self._hit(hwnd, lp) if hit == HTCLIENT else hit
            if msg == WM_PAINT:
                self._paint(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_NCMOUSEMOVE:
                h = wp if wp in (HTMINBUTTON, HTMAXBUTTON, HTCLOSE) else None
                if h != self.hover:
                    self.hover = h
                    self._repaint()
                if not self._tracking:
                    t = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), 0x0002 | 0x0010, hwnd, 0)  # TME_LEAVE | TME_NONCLIENT
                    self._tracking = bool(TrackMouseEvent(ctypes.byref(t)))
                return DefWindowProcW(hwnd, msg, wp, lp)
            if msg == WM_NCMOUSELEAVE:
                self._tracking = False
                if self.hover or self.pressed:
                    self.hover = self.pressed = None
                    self._repaint()
                return 0
            if msg == WM_NCLBUTTONDOWN and wp in (HTMINBUTTON, HTMAXBUTTON, HTCLOSE):
                self.pressed = wp
                self._repaint()
                return 0
            if msg == WM_NCLBUTTONUP and wp in (HTMINBUTTON, HTMAXBUTTON, HTCLOSE):
                hit, self.pressed = (wp if self.pressed == wp else None), None
                self._repaint()
                if hit == HTMINBUTTON:
                    ShowWindow(hwnd, SW_MINIMIZE)
                elif hit == HTMAXBUTTON:
                    ShowWindow(hwnd, SW_RESTORE if IsZoomed(hwnd) else SW_SHOWMAXIMIZED)
                elif hit == HTCLOSE:
                    PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return 0
            if msg == WM_NCACTIVATE:
                self.active = bool(wp)
                self._repaint()
                return DefWindowProcW(hwnd, msg, wp, -1)  # 테두리를 다시 그리지 않게(-1)
            if msg == WM_TIMER and wp == ZOOM_TIMER:
                self._on_zoom()
                return 0
            if msg == WM_TIMER and wp == RETRY_TIMER:
                KillTimer(hwnd, RETRY_TIMER)
                if self.alive() and not self.ctrl and not self.env:
                    self._make_env()
                return 0
            if msg == WM_SIZE:
                self._fit()
                self._repaint(False)
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
