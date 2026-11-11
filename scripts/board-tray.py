"""board-tray.py — Session Orchestrator 트레이 상주 (11-10).

시계 옆 알림 영역(트레이)에 아이콘으로 상주하면서
  1. 상황판 서버(board-server.py)를 품는다. 127.0.0.1:<포트>(board/config.json 의 port, 기본 18799)가 닫혀 있으면 이 프로세스 안에서
     띄우고, 이미 떠 있으면 그걸 쓴다. 그 포트를 못 쓰면 다른 후보로 옮겨 config.json 에 적고, 못 띄우면 이유를 툴팁·메뉴·클릭 알림에 보인다.
     그래서 Claude 앱이나 관제 세션이 꺼져도 실시간 층(상태·토큰·연결)은 계속 돈다.
  2. 아이콘 = 로고 A(레인 막대: 칸반 4열이자 악보 줄). 도는 세션이 있으면 막대가 오르내리고,
     내 답 대기가 있으면 오른쪽 위 빨간 숫자 배지, 서버가 안 잡히면 회색 바탕에 막대가 내려앉는다. 관제 세션은 세지 않는다.
     내 답 대기 = 판정상 「내 답 대기」 + 세션이 작업자에게 질문을 남기고 멈춘 것(실시간, 정리 후보는 제외) − 지금 도는 것.
  3. 윈도우 알림: 질문(세션이 묻고 멈춤) · 턴 끝남(20초 넘게 돌던 세션) · 멈춤(도구 호출 뒤 1분 넘게 응답 없음 = 승인 대기이거나 긴 작업)
     · 세션 간 요청 30분째 미처리 · 갱신 뒤 새로 생긴 내 답 대기. 알림을 누르면 상황판을 연다(창이 없으면 그 세션 상세로).
  4. 왼쪽 클릭 = 상황판 열기(Edge 앱 창, 이미 떠 있으면 앞으로). 오른쪽 클릭 = 메뉴(열기 · 알림 받기 · 로그인 시 자동 실행 · 종료).

실행
  pythonw -I scripts/board-tray.py             트레이 시작(콘솔 없음). 이미 떠 있으면 바로 끝난다(이름 있는 뮤텍스).
  python  -I scripts/board-tray.py --install   런처 바로가기(board/Session Orchestrator.lnk)와 로그인 자동 실행 바로가기를 만들고
                                               explorer.exe 로 트레이를 띄운다. Claude 앱 세션에서 바로 띄우면 앱이 꺼질 때 같이 꺼질 수 있어서다.
  python  -I scripts/board-tray.py --uninstall 로그인 자동 실행 바로가기만 지운다(트레이가 떠 있으면 메뉴 「종료」로 끈다).
  python  -I scripts/board-tray.py --make-icon  로고 아이콘 파일(scripts/board-logo.ico, 16~256px)을 다시 만든다. 바로가기 아이콘으로 쓴다.

설정 board/tray-settings.json(알림·자동 실행), 로그 board/tray.log · board/tray-server.log. board/ 는 gitignore.
외부 패키지 없이 표준 라이브러리 + ctypes(Win32) 만 쓴다.
"""
import ctypes
import ctypes.wintypes as W
import hashlib
import importlib.util
import json
import re
import os
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import winreg
import zlib
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))  # exe(PyInstaller)로 실행 중이면 True. 코드는 exe 안, 데이터는 SO_HOME
WS = Path(os.environ.get("SO_HOME") or HERE.parent)  # 데이터 폴더(board/ 의 부모). exe 는 SO_HOME 으로 넘겨받는다
ROOT = WS / "board"
CONFIG = ROOT / "config.json"


def read_config() -> dict:
    try:
        d = json.loads(CONFIG.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def valid_port(v, default=18799) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return v if 1024 <= v <= 65535 else default


# 포트는 board/config.json 의 "port"(없으면 18799). 그 포트를 못 쓰면(다른 프로그램이 씀, 또는 Hyper-V·WSL·Docker 가
# Windows 에 예약한 범위 = WinError 10013) 아래 후보로 옮기고 config.json 에 적는다
PORT = valid_port(read_config().get("port"))
BASE = f"http://127.0.0.1:{PORT}"
ALT_PORTS = (18799, 28799, 38799, 17799, 27799, 37799)
TITLE = "Session Orchestrator"          # 상황판 페이지 <title> = Edge 앱 창 제목
LNK_NAME = "Session Orchestrator.lnk"
ICON = HERE / "board-logo.ico"           # 바로가기 아이콘(로고 A). 없으면 --make-icon 이나 --install 이 만든다
ANIM_MS = 380                             # 도는 동안 막대 애니메이션 간격
SETTINGS = ROOT / "tray-settings.json"
LOG = ROOT / "tray.log"
POLL_MS = 3000
TURN_MIN_WORK = 20        # 이만큼(초) 넘게 돌던 세션이 턴을 끝내면 알림
UNREAD_ALERT = 30 * 60    # 세션 간 요청이 미처리로 이만큼 지나면 알림
TOAST_GAP = 6             # 알림 사이 최소 간격(초). 그새 3개 넘게 쌓이면 하나로 묶는다
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def make_writable(path: Path) -> str:
    """덮어쓰기 전에 기존 파일의 읽기 전용 · 숨김 · 시스템 속성을 지운다. 지우기 전 속성을 글로 돌려준다(오류 메시지용).
    일부 PC 에서는 이 도구가 만든 파일 일부에 이런 속성이 붙어 덮어쓰기가 「액세스 거부」, 읽기 전용 바로가기는 WScript 가 저장 못 함(실측)."""
    try:
        a = ctypes.windll.kernel32.GetFileAttributesW(str(path)) & 0xFFFFFFFF  # ctypes 기본은 부호 있는 int 라 '없음'이 -1 로 온다
        if a == 0xFFFFFFFF:  # 없음(INVALID_FILE_ATTRIBUTES)
            return ""
        names = [n for bit, n in ((1, "읽기 전용"), (2, "숨김"), (4, "시스템")) if a & bit]
        if names:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)  # FILE_ATTRIBUTE_NORMAL
        return "·".join(names)
    except Exception:
        return ""


def log(*a):
    line = time.strftime("%m-%d %H:%M:%S ") + " ".join(str(x) for x in a) + "\n"
    for _ in range(2):  # 로그 파일이 쓰기 막힘 속성이면 한 번 풀고 다시
        try:
            if LOG.exists() and LOG.stat().st_size > 512_000:
                LOG.replace(LOG.with_suffix(".log.1"))
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line)
            return
        except OSError:
            make_writable(LOG)


def vtuple(s: str) -> tuple:
    """'v1.2.0' · '1.2' → (1, 2, 0). 버전 비교용."""
    nums = [int(x) for x in re.findall(r"\d+", s or "")[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def register_app_identity() -> None:
    """알림에 「Session Orchestrator」 이름과 로고가 보이게 앱 ID 를 사용자 레지스트리에 등록하고 이 프로세스에 붙인다.
    (패키지로 깔린 Store 판 Python 으로 돌면 Windows 가 그 패키지 이름을 우선할 수 있다 — exe 설치는 해당 없음)"""
    icon = ROOT / "board-logo.png"
    try:
        if not icon.exists():
            icon.write_bytes(_png(128, logo_rgba(128)))
    except OSError as e:
        log("app id icon:", e)
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\Classes\\AppUserModelId\\" + AUMID) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_EXPAND_SZ, TITLE)
            winreg.SetValueEx(k, "IconUri", 0, winreg.REG_EXPAND_SZ, str(icon))
    except OSError as e:
        log("app id:", e)
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception as e:
        log("app id set:", repr(e))


def startup_lnk() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / LNK_NAME


def pythonw() -> str:
    if FROZEN:  # exe 면 exe 자신이 트레이를 띄운다
        return sys.executable
    p = Path(sys.executable)
    w = p.with_name("pythonw.exe")
    return str(w if w.exists() else p)


def make_lnk(path: Path, target: str, args: str, workdir: str, desc: str):
    """WScript.Shell 로 바로가기를 만든다. 값은 환경변수로 넘겨 따옴표 문제를 피한다. 아이콘은 로고 A(.ico)."""
    if not FROZEN and not ICON.exists():
        write_ico(ICON)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:SO_LNK);"
          "$s.TargetPath=$env:SO_TARGET;$s.Arguments=$env:SO_ARGS;$s.WorkingDirectory=$env:SO_DIR;"
          "$s.Description=$env:SO_DESC;$s.IconLocation=$env:SO_ICON;$s.WindowStyle=7;$s.Save()")
    env = dict(os.environ, SO_LNK=str(path), SO_TARGET=target, SO_ARGS=args, SO_DIR=workdir, SO_DESC=desc, SO_ICON=f"{sys.executable if FROZEN else ICON},0")

    def run():
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                              env=env, creationflags=CREATE_NO_WINDOW, timeout=30, capture_output=True)

    attrs = make_writable(path)  # 읽기 전용 바로가기는 덮어써지지 않는다(11-11 실측)
    r = run()
    if r.returncode != 0 and path.exists():  # 그래도 안 되면 옛 바로가기를 지우거나 비켜 두고 한 번 더
        try:
            path.unlink()
        except OSError:
            try:
                path.rename(path.with_name(path.name + ".old-" + time.strftime("%Y%m%d-%H%M%S")))
            except OSError:
                pass
        r = run()
    if r.returncode != 0:
        raw = (r.stderr or b"") + (r.stdout or b"")
        try:
            msg = raw.decode("utf-8")
        except UnicodeDecodeError:
            msg = raw.decode(f"cp{ctypes.windll.kernel32.GetOEMCP()}", "replace")
        msg = " ".join(x.strip() for x in msg.splitlines() if x.strip())[:300]
        raise RuntimeError(f"바로가기를 만들지 못함: {path}" + (f" (원래 속성: {attrs})" if attrs else "") + f" — {msg}")
    return attrs  # 풀어 준 속성(없으면 빈 글자). 설치 때 원인 짐작용으로 출력


def tray_lnk_args() -> str:
    if FROZEN:
        return "tray"
    return f'-I "{HERE / "board-tray.py"}"'


# exe 의 로그인 자동 실행은 바로가기 대신 레지스트리 Run 값(PowerShell 을 띄우지 않게)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "SessionOrchestrator"


def autostart_on() -> bool:
    if FROZEN:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, RUN_NAME)
            return True
        except OSError:
            return False
    return startup_lnk().exists()


def set_autostart(on: bool):
    if FROZEN:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" tray')
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except OSError:
                    pass
        return
    p = startup_lnk()
    if on:
        make_lnk(p, pythonw(), tray_lnk_args(), str(WS), "Session Orchestrator 트레이(로그인 시 자동 실행)")
    elif p.exists():
        p.unlink()


# ── 설정 ──
def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(s: dict):
    try:
        make_writable(SETTINGS)
        SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log("settings:", e)


# ── Win32 ──
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
ENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON), ("hCursor", W.HANDLE),
                ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR), ("hIconSm", W.HICON)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT), ("uCallbackMessage", W.UINT),
                ("hIcon", W.HICON), ("szTip", W.WCHAR * 128), ("dwState", W.DWORD), ("dwStateMask", W.DWORD),
                ("szInfo", W.WCHAR * 256), ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", W.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD), ("biBitCount", W.WORD),
                ("biCompression", W.DWORD), ("biSizeImage", W.DWORD), ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG),
                ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", W.DWORD * 1)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", W.BOOL), ("xHotspot", W.DWORD), ("yHotspot", W.DWORD), ("hbmMask", W.HBITMAP), ("hbmColor", W.HBITMAP)]


def _p(dll, name, res, *args):
    f = getattr(dll, name)
    f.restype, f.argtypes = res, list(args)
    return f


DefWindowProcW = _p(user32, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
RegisterClassExW = _p(user32, "RegisterClassExW", W.ATOM, ctypes.POINTER(WNDCLASSEXW))
CreateWindowExW = _p(user32, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                     ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID)
GetMessageW = _p(user32, "GetMessageW", W.BOOL, ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
TranslateMessage = _p(user32, "TranslateMessage", W.BOOL, ctypes.POINTER(W.MSG))
DispatchMessageW = _p(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(W.MSG))
PostQuitMessage = _p(user32, "PostQuitMessage", None, ctypes.c_int)
SetTimer = _p(user32, "SetTimer", ctypes.c_size_t, W.HWND, ctypes.c_size_t, W.UINT, ctypes.c_void_p)
KillTimer = _p(user32, "KillTimer", W.BOOL, W.HWND, ctypes.c_size_t)
RegisterWindowMessageW = _p(user32, "RegisterWindowMessageW", W.UINT, W.LPCWSTR)
CreatePopupMenu = _p(user32, "CreatePopupMenu", W.HMENU)
AppendMenuW = _p(user32, "AppendMenuW", W.BOOL, W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR)
SetMenuDefaultItem = _p(user32, "SetMenuDefaultItem", W.BOOL, W.HMENU, W.UINT, W.UINT)
TrackPopupMenu = _p(user32, "TrackPopupMenu", W.BOOL, W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, ctypes.c_void_p)
DestroyMenu = _p(user32, "DestroyMenu", W.BOOL, W.HMENU)
GetCursorPos = _p(user32, "GetCursorPos", W.BOOL, ctypes.POINTER(W.POINT))
SetForegroundWindow = _p(user32, "SetForegroundWindow", W.BOOL, W.HWND)
PostMessageW = _p(user32, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
EnumWindows = _p(user32, "EnumWindows", W.BOOL, ENUMPROC, W.LPARAM)
GetWindowTextW = _p(user32, "GetWindowTextW", ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
GetClassNameW = _p(user32, "GetClassNameW", ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
IsWindowVisible = _p(user32, "IsWindowVisible", W.BOOL, W.HWND)
IsIconic = _p(user32, "IsIconic", W.BOOL, W.HWND)
ShowWindow = _p(user32, "ShowWindow", W.BOOL, W.HWND, ctypes.c_int)
GetSystemMetrics = _p(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
CreateIconIndirect = _p(user32, "CreateIconIndirect", W.HICON, ctypes.POINTER(ICONINFO))
DestroyIcon = _p(user32, "DestroyIcon", W.BOOL, W.HICON)
DrawTextW = _p(user32, "DrawTextW", ctypes.c_int, W.HDC, W.LPCWSTR, ctypes.c_int, ctypes.POINTER(W.RECT), W.UINT)
GetDC = _p(user32, "GetDC", W.HDC, W.HWND)
ReleaseDC = _p(user32, "ReleaseDC", ctypes.c_int, W.HWND, W.HDC)
Shell_NotifyIconW = _p(shell32, "Shell_NotifyIconW", W.BOOL, W.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
CreateCompatibleDC = _p(gdi32, "CreateCompatibleDC", W.HDC, W.HDC)
CreateDIBSection = _p(gdi32, "CreateDIBSection", W.HBITMAP, W.HDC, ctypes.POINTER(BITMAPINFO), W.UINT,
                      ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD)
CreateBitmap = _p(gdi32, "CreateBitmap", W.HBITMAP, ctypes.c_int, ctypes.c_int, W.UINT, W.UINT, ctypes.c_void_p)
SelectObject = _p(gdi32, "SelectObject", W.HGDIOBJ, W.HDC, W.HGDIOBJ)
DeleteObject = _p(gdi32, "DeleteObject", W.BOOL, W.HGDIOBJ)
DeleteDC = _p(gdi32, "DeleteDC", W.BOOL, W.HDC)
CreateFontW = _p(gdi32, "CreateFontW", W.HFONT, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.DWORD,
                 W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR)
SetTextColor = _p(gdi32, "SetTextColor", W.COLORREF, W.HDC, W.COLORREF)
SetBkMode = _p(gdi32, "SetBkMode", ctypes.c_int, W.HDC, ctypes.c_int)
GdiFlush = _p(gdi32, "GdiFlush", W.BOOL)
CreateMutexW = _p(kernel32, "CreateMutexW", W.HANDLE, W.LPVOID, W.BOOL, W.LPCWSTR)
GetModuleHandleW = _p(kernel32, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)

WM_DESTROY, WM_COMMAND, WM_TIMER, WM_APP = 0x0002, 0x0111, 0x0113, 0x8000
WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0205, 0x007B
NIN_SELECT, NIN_KEYSELECT, NIN_BALLOONUSERCLICK = 0x0400, 0x0401, 0x0405
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x01, 0x02, 0x04, 0x10, 0x80
NIIF_INFO, NIIF_USER, NIIF_LARGE_ICON = 0x01, 0x04, 0x20
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0, 0x800, 0x8, 0x1
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
DT_CENTER, DT_VCENTER, DT_SINGLELINE, DT_NOPREFIX = 0x1, 0x4, 0x20, 0x800
CB_MSG = WM_APP + 1
WM_TEST_TOAST = WM_APP + 0x30   # `SessionOrchestrator.exe notify-test` 가 보낸다(알림 모양 확인용)
WM_UPDATE_CHECK = WM_APP + 0x31  # `SessionOrchestrator.exe update-check` 가 보낸다
ID_OPEN, ID_NOTIFY, ID_AUTOSTART, ID_TEST, ID_AUTOUPD, ID_UPDCHK, ID_QUIT = 1, 2, 3, 4, 5, 6, 9

# 알림에 보일 앱 이름 · 로고. Store 판 Python 으로 돌던 트레이는 그 패키지(Python)의 이름 · 로고로 알림이 갔다(Windows 알림 기록으로 확인)
AUMID = "SessionOrchestrator.Tray"
# 자동 업데이트(exe 만): 공개 레포의 최신 릴리스를 켤 때와 6시간마다 확인
REPO = "Baekdaniell/session-orchestrator"
UPDATE_EVERY = 6 * 3600
VERSION = os.environ.get("SO_VERSION", "")  # exe 시작점(so_app)이 넘겨준다. Python 으로 돌면 빈 값 → 자동 업데이트 안 함


CORAL, GRAY, BADGE, WHITE = (255, 94, 91), (138, 138, 146), (214, 54, 64), (255, 255, 255)
# 로고 A(레인 막대): 32 단위 격자 위 막대 4개의 높이. 폭 4 · 간격 2 · 가운데 정렬(32px 에서 x=5,11,17,23) · 바닥선 y=26 · 둥근 끝.
# 페이지 favicon·제목의 SVG 와 같은 모양. 작은 크기에서 흐려지지 않게 막대 위치·폭은 크기마다 정수 픽셀로 맞춘다
BARS = (12, 19, 10, 16)
FRAMES = ((1, 1, 1, 1), (.6, 1.12, .75, 1.25), (1.25, .65, 1.3, .7), (.85, .9, 1.1, .85))  # 도는 동안 번갈아 그리는 높이 배율


def _in_rr(x, y, x0, y0, x1, y1, r):
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def logo_rgba(size: int, frame: int = 0, offline: bool = False, badge: bool = False) -> bytes:
    """로고 A 를 size×size RGBA(위→아래, 알파 직선)로. 슈퍼샘플로 가장자리를 부드럽게. badge=오른쪽 위 빨간 원(숫자는 make_icon 이 GDI 로)."""
    k = size / 32
    bg = GRAY if offline else CORAL
    w = max(2, round(4 * k))
    gap = max(1, round(1.75 * k))
    x0 = round((size - (4 * w + 3 * gap)) / 2)
    base = round(26 * k)
    bars = []
    for i, h in enumerate(BARS):
        hh = 5 if offline else min(21, h * FRAMES[frame % len(FRAMES)][i])
        x = x0 + i * (w + gap)
        bars.append((x, base - max(w, round(hh * k)), x + w, base, w / 2))
    br = size * 0.31
    bcx, bcy = size - br, br
    ring = max(1.0, size / 16)
    ss = 4 if size <= 64 else 2
    out = bytearray(size * size * 4)
    for py in range(size):
        for px in range(size):
            r = g = b = n = 0
            for sy in range(ss):
                y = py + (sy + .5) / ss
                for sx in range(ss):
                    x = px + (sx + .5) / ss
                    col = None
                    d2 = (x - bcx) ** 2 + (y - bcy) ** 2
                    if badge and d2 <= br * br:
                        col = BADGE if d2 <= (br - ring) ** 2 else WHITE
                    elif _in_rr(x, y, 0, 0, size, size, 8 * k):
                        col = bg
                        for bb in bars:
                            if _in_rr(x, y, *bb):
                                col = WHITE
                                break
                    if col:
                        r += col[0]; g += col[1]; b += col[2]; n += 1
            if n:
                i = (py * size + px) * 4
                out[i:i + 4] = bytes((r // n, g // n, b // n, n * 255 // (ss * ss)))
    return bytes(out)


def _png(size: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def write_ico(path: Path, sizes=(16, 20, 24, 32, 40, 48, 64, 128, 256)):
    """로고 A 를 여러 크기 PNG 로 담은 .ico (바로가기·탐색기 아이콘)."""
    imgs = [(n, _png(n, logo_rgba(n))) for n in sizes]
    off = 6 + 16 * len(imgs)
    dirs, data = b"", b""
    for n, png in imgs:
        dirs += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(png), off + len(data))
        data += png
    make_writable(path)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(imgs)) + dirs + data)


_icon_cache = {}


def make_icon(size: int, frame: int = 0, offline: bool = False, badge: str = ""):
    """트레이용 HICON(같은 조합은 캐시해 다시 쓴다 — 애니메이션 프레임마다 다시 그리지 않게)."""
    key = (size, frame, offline, badge)
    if key in _icon_cache:
        return _icon_cache[key]
    rgba = logo_rgba(size, frame, offline, bool(badge))
    screen = GetDC(None)
    dc = CreateCompatibleDC(screen)
    bmi = BITMAPINFO()
    h = bmi.bmiHeader
    h.biSize, h.biWidth, h.biHeight, h.biPlanes, h.biBitCount = ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32
    bits = ctypes.c_void_p()
    hbm = CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    px = (ctypes.c_uint32 * (size * size)).from_address(bits.value)
    for i in range(size * size):
        r, g, b, a = rgba[i * 4:i * 4 + 4]
        px[i] = (a << 24) | (r << 16) | (g << 8) | b
    if badge:  # 배지 숫자(흰색 굵게). GDI 가 그린 칸은 알파가 0 이 되므로 아래에서 되살린다
        old = SelectObject(dc, hbm)
        br = size * 0.31
        font = CreateFontW(-max(7, int(br * (1.45 if len(badge) == 1 else 1.05))), 0, 0, 0, 800, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI")
        oldf = SelectObject(dc, font)
        SetBkMode(dc, 1)
        SetTextColor(dc, 0x00FFFFFF)
        rc = W.RECT(int(size - 2 * br), 0, size, int(2 * br) + 1)
        DrawTextW(dc, badge, -1, ctypes.byref(rc), DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX)
        GdiFlush()
        SelectObject(dc, oldf)
        SelectObject(dc, old)
        DeleteObject(font)
        for i in range(size * size):
            px[i] = (rgba[i * 4 + 3] << 24) | (px[i] & 0xFFFFFF)
    mask = CreateBitmap(size, size, 1, 1, ctypes.create_string_buffer(((size + 15) // 16) * 2 * size))
    info = ICONINFO(True, 0, 0, mask, hbm)
    hicon = CreateIconIndirect(ctypes.byref(info))
    DeleteObject(mask)
    DeleteObject(hbm)
    DeleteDC(dc)
    ReleaseDC(None, screen)
    _icon_cache[key] = hicon
    return hicon


def edge_path() -> str:
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as k:
                v = winreg.QueryValue(k, None)
                if v and Path(v).exists():
                    return v
        except OSError:
            pass
    for v in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if Path(v).exists():
            return v
    return ""


def find_board_window():
    """제목이 정확히 'Session Orchestrator' 인 Edge(크로뮴) 창 = --app 창. 일반 탭 창은 제목 뒤에 ' - Microsoft Edge' 가 붙어 걸리지 않는다."""
    found = []
    buf = ctypes.create_unicode_buffer(256)

    def cb(hwnd, _):
        if IsWindowVisible(hwnd):
            GetWindowTextW(hwnd, buf, 256)
            if buf.value == TITLE:
                GetClassNameW(hwnd, buf, 256)
                if buf.value.startswith("Chrome_WidgetWin"):
                    found.append(hwnd)
                    return False
        return True

    EnumWindows(ENUMPROC(cb), 0)
    return found[0] if found else None


def md_ts(s) -> float:
    """판정 데이터의 "MM-DD HH:MM" → epoch(올해, 미래로 나오면 작년). 못 읽으면 0."""
    try:
        now = time.localtime()
        t = time.mktime((now.tm_year, int(s[:2]), int(s[3:5]), int(s[6:8]), int(s[9:11]), 0, 0, 0, -1))
        return t - 365 * 86400 if t - time.time() > 86400 else t
    except (TypeError, ValueError, IndexError):
        return 0


def first_line(s: str, n=110) -> str:
    s = (s or "").strip().splitlines()[0] if (s or "").strip() else ""
    return s if len(s) <= n else s[: n - 1] + "…"


class Board:
    """board/board-sessions.json(판정 결과)을 mtime 이 바뀔 때만 다시 읽는다."""

    def __init__(self):
        self.mtime = None
        self.rows = []
        self.title = {}
        self.control = ""

    def reload(self):
        p = ROOT / "board-sessions.json"
        try:
            mt = p.stat().st_mtime
        except OSError:
            return
        if mt == self.mtime:
            return
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log("board json:", e)
            return
        self.mtime, self.rows, self.title = mt, rows, {}
        for r in rows:
            link = r.get("link") or ""
            sid = link.rsplit("/", 1)[-1] if "local_" in link else ""
            r["_sid"] = sid
            if sid:
                self.title[sid] = r.get("session", sid)
                if r.get("status") == "관제":
                    self.control = sid

    def waiting(self):
        return [r for r in self.rows if r.get("status") == "내 답 대기"]


class Tray:
    def __init__(self):
        self.settings = load_settings()
        self.board = Board()
        self.hwnd = None
        self.hicon = None
        self.icon_key = None
        self.httpd = None
        self.own_server = False
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 시스템 프록시 무시(127.0.0.1)
        self.prev = {}
        self.work_since = {}
        self.seen_edges = set()
        self.seen_wait = None
        self.toasts = deque()
        self.last_toast = 0.0
        self.toast_sid = None
        self.last_open = 0.0
        self.first = True
        self._updating = False  # 자동 업데이트가 겹쳐 돌지 않게
        self.status = "시작 중"
        self.armed = False
        self.size = GetSystemMetrics(49) or 16  # SM_CXSMICON
        self.live_titles = {}
        self.count = self.running = 0
        self.ok = False
        self.animating = False
        self.frame = 0
        self.taskbar_msg = RegisterWindowMessageW("TaskbarCreated")
        self._wndproc = WNDPROC(self.wndproc)  # 참조를 붙들어 둔다(GC 되면 튕김)

    # ── 서버 ──
    def get(self, path, token=False, timeout=2.0):
        req = urllib.request.Request(BASE + path)
        if token:
            try:
                req.add_header("X-Board-Token", (ROOT / ".board-token").read_text(encoding="utf-8").strip())
            except OSError:
                pass
        with self.opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def set_status(self, s):
        """서버를 못 띄운 이유. 툴팁·메뉴·클릭 알림에 보이고, 바뀔 때만 로그에 남긴다(3초마다 같은 줄이 쌓이지 않게)."""
        if s != self.status:
            log("server:", s)
        self.status = s

    def bind(self, mod):
        """포트를 잡는다. config 포트를 못 쓰면 ALT_PORTS 를 차례로 시도하고, 옮긴 포트는 config.json 에 적는다.
        config 포트가 '사용 중'인데 우리 서버가 거기서 답하면(관제가 직접 띄운 서버 등) None — 그 서버를 쓴다."""
        global PORT, BASE
        tried = []
        for p in dict.fromkeys((PORT,) + ALT_PORTS):
            try:
                httpd = mod.Server(("127.0.0.1", p), mod.Handler)  # 포트를 혼자 잡는 서버(남이 쓰는 포트면 여기서 오류)
            except OSError as e:
                code = getattr(e, "winerror", None) or e.errno
                tried.append(f"{p}(오류 {code})")
                if p == PORT and code in (10048, 98):
                    try:
                        self.get("/api/health", timeout=3)
                        return None
                    except Exception:
                        pass
                continue
            if p != PORT:
                log("port:", PORT, "못 씀 →", p, "로 옮김 · 시도:", ", ".join(tried))
                PORT, BASE = p, f"http://127.0.0.1:{p}"
                cfg = read_config()
                cfg["port"] = p
                try:
                    make_writable(CONFIG)
                    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
                except OSError as e:
                    log("config 쓰기:", e)
            return httpd
        raise OSError("쓸 수 있는 포트가 없음 — 시도: " + ", ".join(tried))

    def start_server(self) -> bool:
        if not (ROOT / "session-board.html").exists():
            self.set_status("상황판 파일 없음 — install.cmd 를 다시 실행하세요")
            return False
        httpd = None
        try:
            spec = importlib.util.spec_from_file_location("board_server", HERE / "board-server.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for d in mod.STATES:
                (ROOT / d).mkdir(exist_ok=True)
            mod.Handler.root = ROOT
            # 포트를 먼저 잡는다. 못 잡으면 대화 파일 감시 스레드를 띄우지 않는다(전엔 실패할 때마다 3초에 하나씩 새로 떴음)
            httpd = self.bind(mod)
            if httpd is None:
                self.set_status("다른 곳에서 띄운 서버를 씀")
                return False
            target = mod.board_live.watched_workspace(ROOT)  # 감시할 작업 폴더(board/config.json, 없으면 이 도구의 부모 폴더)
            mod.Handler.app = mod.AppSessions(target)  # 앱 세션 기록으로 판정 전 세션 짝짓기·보관 제외
            mod.Handler.tracker = mod.board_live.Tracker(target, days=7, interval=1.5)
            mod.Handler.tracker.start()
        except Exception as e:  # 포트 · 파일 · 파이썬 버전 문제 무엇이든 이유를 남기고 다음 주기에 다시
            if httpd is not None:
                httpd.server_close()
            self.set_status(f"서버를 못 띄움: {e}"[:200])
            return False
        self.httpd = httpd
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="board-server").start()
        self.own_server = True
        self.set_status("이 트레이가 띄움")
        log("server: 이 트레이 안에서 띄움", BASE)
        return True

    def ensure_server(self) -> bool:
        try:
            h = self.get("/api/health", timeout=1.5)
            self.armed = bool(h.get("armed"))
            return True
        except Exception:
            if self.httpd is None and self.start_server():
                time.sleep(0.3)
                try:
                    self.armed = bool(self.get("/api/health", timeout=1.5).get("armed"))
                    return True
                except Exception:
                    pass
            return False

    # ── 트레이 아이콘 ──
    def nid(self, flags):
        n = NOTIFYICONDATAW()
        n.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        n.hWnd, n.uID, n.uFlags, n.uCallbackMessage = self.hwnd, 1, flags, CB_MSG
        return n

    def add_icon(self):
        n = self.nid(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        n.hIcon = self.hicon
        n.szTip = TITLE
        Shell_NotifyIconW(NIM_ADD, ctypes.byref(n))
        n.uVersion = 4
        Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(n))

    def set_icon(self, count: int, running: int, ok: bool, tip: str):
        self.count, self.running, self.ok = count, running, ok
        if running and ok and not self.animating:  # 도는 세션이 있으면 막대가 오르내린다
            SetTimer(self.hwnd, 2, ANIM_MS, None)
            self.animating = True
        elif (not running or not ok) and self.animating:
            KillTimer(self.hwnd, 2)
            self.animating = False
            self.frame = 0
        self.paint_icon()
        n = self.nid(NIF_TIP | NIF_SHOWTIP)
        n.szTip = tip[:127]
        Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(n))

    def paint_icon(self):
        many = "9+" if self.size >= 20 else "+"  # 16px 에선 두 글자가 안 읽혀 + 하나로
        badge = "" if not self.ok or not self.count else (str(self.count) if self.count < 10 else many)
        key = (self.frame if self.animating else 0, not self.ok, badge)
        if key == self.icon_key:
            return
        self.icon_key = key
        self.hicon = make_icon(self.size, key[0], key[1], badge)
        n = self.nid(NIF_ICON)
        n.hIcon = self.hicon
        Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(n))

    def show_toast(self, title, body, sid):
        n = self.nid(NIF_INFO)
        n.szInfoTitle = title[:63]
        n.szInfo = (body or " ")[:255]
        # 알림 안의 그림을 Windows 기본 「i」 대신 로고로(큰 아이콘)
        n.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
        n.hBalloonIcon = make_icon(GetSystemMetrics(11) or 32, 0, False, "")  # SM_CXICON
        Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(n))
        self.toast_sid = sid
        self.last_toast = time.time()

    def test_toast(self):
        self.show_toast("🎼 알림 시험", "이렇게 떠요. 안 보이면 Windows 설정 → 시스템 → 알림에서 Session Orchestrator 알림을 켜 주세요.", None)

    def announce_update(self):
        """방금 자동 업데이트로 바뀌었으면(설치기가 board/updated.txt 에 남김) 한 번 알린다."""
        f = ROOT / "updated.txt"
        try:
            v = f.read_text(encoding="utf-8").strip()
            f.unlink()
        except OSError:
            return
        if v:
            self.show_toast(f"✅ v{v} 로 업데이트했어요", "상황판이 다시 떴어요. 바뀐 점은 GitHub 릴리스에서 볼 수 있어요.", None)

    # ── 자동 업데이트(exe 만) ──
    def update_loop(self):
        time.sleep(90)  # 로그인 직후에 몰리지 않게
        while True:
            if self.settings.get("autoupdate", True):
                self.check_update(False)
            time.sleep(UPDATE_EVERY)

    def check_update(self, manual: bool):
        """공개 레포의 최신 릴리스가 지금보다 새 버전이면 exe 를 받아 크기 · SHA-256 을 맞춰 본 뒤 실행해 바꿔 끼운다.
        받은 exe 는 설치 폴더 밖 경로라 「설치 · 업데이트」로 돌고, 이 트레이를 닫고 새 트레이를 띄운다."""
        if not VERSION or self._updating:
            return
        self._updating = True
        try:
            ua = {"User-Agent": f"SessionOrchestrator/{VERSION}"}
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers=dict(ua, Accept="application/vnd.github+json"))
            with urllib.request.urlopen(req, timeout=20) as r:  # 시스템 프록시를 따른다
                rel = json.loads(r.read().decode("utf-8"))
            tag = str(rel.get("tag_name") or "")
            if vtuple(tag) <= vtuple(VERSION):
                log("update: 최신", VERSION, "(릴리스", tag + ")")
                if manual:
                    self.show_toast("✅ 최신 버전이에요", f"지금 v{VERSION} 이 가장 새 버전이에요.", None)
                return
            asset = next((a for a in rel.get("assets") or [] if a.get("name") == "SessionOrchestrator.exe"), None)
            if not asset:
                log("update: 릴리스에 exe 가 없음", tag)
                return
            d = WS / "update"
            d.mkdir(exist_ok=True)
            part, exe = d / f"SessionOrchestrator-{tag}.exe.part", d / f"SessionOrchestrator-{tag}.exe"
            h, n = hashlib.sha256(), 0
            with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"], headers=ua), timeout=120) as r, open(part, "wb") as f:
                while True:
                    b = r.read(1 << 16)
                    if not b:
                        break
                    f.write(b)
                    h.update(b)
                    n += len(b)
            want = str(asset.get("digest") or "")
            if n != int(asset.get("size") or -1) or (want.startswith("sha256:") and want[7:].lower() != h.hexdigest()):
                part.unlink(missing_ok=True)
                log("update: 받은 파일이 릴리스와 다름", n, asset.get("size"), want[:24])
                if manual:
                    self.show_toast("⚠ 업데이트를 멈췄어요", "받은 파일이 릴리스와 달라요. 누르면 로그가 열려요.", "__log__")
                return
            make_writable(exe)
            if exe.exists():
                exe.unlink()
            part.rename(exe)
            log("update: 설치 시작", VERSION, "→", tag)
            self.show_toast(f"🔄 {tag} 로 업데이트해요", "1~2분 뒤 트레이가 다시 떠요. 그동안 상황판이 잠깐 꺼져요.", None)
            subprocess.Popen([str(exe), "--update"], cwd=str(WS), close_fds=True, creationflags=DETACHED_PROCESS | 0x00000200)
        except Exception as e:
            log("update:", repr(e))
            if manual:
                self.show_toast("⚠ 업데이트 확인 실패", f"{e}"[:200], "__log__")
        finally:
            self._updating = False

    def queue(self, title, body, sid=None):
        if self.settings.get("notify", True):
            self.toasts.append((title, body, sid))

    def pump_toasts(self):
        if not self.toasts or time.time() - self.last_toast < TOAST_GAP:
            return
        if len(self.toasts) >= 3:
            items = list(self.toasts)
            self.toasts.clear()
            self.show_toast(f"🎼 알림 {len(items)}개", "\n".join(t for t, _, _ in items), None)
        else:
            self.show_toast(*self.toasts.popleft())

    # ── 주기 작업 ──
    def name(self, sid):
        if sid in self.board.title:
            return self.board.title[sid]
        t = self.live_titles.get(sid)  # 판정 전 새 세션: 대화 파일의 사이드바 제목
        return f"{t} (판정 전)" if t else "새 세션(판정 전)"

    def tick(self):
        ok = self.ensure_server()
        self.board.reload()
        snap = None
        if ok:
            try:
                snap = self.get("/api/live", token=True, timeout=2.5)
            except Exception as e:
                log("live:", e)
        ctl = self.board.control
        waiting = self.board.waiting()
        sessions = (snap or {}).get("sessions") or {}
        live = {sid: e for sid, e in sessions.items() if sid != ctl}
        for u in (snap or {}).get("unmapped") or []:
            live.setdefault("cli:" + u.get("cli", ""), u)
        self.live_titles = {k: e.get("title") for k, e in live.items() if e.get("title")}
        running = [k for k, e in live.items() if e.get("state") in ("working", "waiting_tool")]
        asking = {k for k, e in live.items() if e.get("state") == "asking" and not (e.get("ask") or {}).get("weak")}
        # 내 답 대기 = 판정상 내 답 대기 + 질문을 남기고 멈춘 세션(정리 후보 제외) − 지금 도는 세션. 상황판 열 규칙과 같다
        eff = set()
        gone = set((snap or {}).get("gone") or [])  # 앱에서 삭제·보관된 세션
        for r in self.board.rows:
            sid, st = r.get("_sid"), r.get("status")
            if not sid or st == "관제" or sid in gone:
                continue
            ls = (live.get(sid) or {}).get("state")
            if ls == "asking" and ((live.get(sid) or {}).get("ask") or {}).get("weak"):
                ls = "idle"  # 글로 추정했는데 앱 판정이 없는 질문은 세지 않는다
            if ls in ("working", "waiting_tool"):
                continue
            jt = md_ts(r.get("judged_at"))
            fresh = ((live.get(sid) or {}).get("ts") or 0) > jt  # 판정 뒤에 다시 움직임
            replied = ((live.get(sid) or {}).get("uts") or 0) > jt  # 판정 뒤에 작업자가 직접 답함
            if ls == "asking" and (st != "정리 후보" or fresh):
                eff.add(sid)
            elif st == "내 답 대기" and not (ls == "idle" and replied):
                eff.add(sid)
        eff |= {k for k in asking if k.startswith("cli:")}
        eff |= {k for k in asking if k.startswith("local_") and k not in self.board.title}  # 앱 기록으로 짝지어진 판정 전 세션
        t = ((snap or {}).get("totals") or {}).get("today") or {}
        out = t.get("output_tokens", 0)
        if not ok:
            tip = f"{TITLE}\n서버에 연결 못 함 — {self.status}"
        else:
            tip = (f"{TITLE}\n내 답 대기 {len(eff)}(질문 {len(asking & eff)}) · 도는 세션 {len(running)} · 오늘 출력 {out / 1000:.0f}k"
                   + ("" if self.armed else "\n관제 세션 대기 아님(채팅 릴레이 멈춤)"))
        self.set_icon(len(eff), len(running), ok, tip)
        if snap and snap.get("ready"):
            self.notify(live, (snap or {}).get("edges") or [], waiting)
        self.pump_toasts()

    def notify(self, live, edges, waiting):
        now = time.time()
        quiet = self.first  # 첫 폴링은 기준만 잡고 알리지 않는다
        for k, e in live.items():
            st, prev = e.get("state"), self.prev.get(k)
            if st != "idle":
                self.work_since.setdefault(k, now)
            if not quiet:
                sid = k if k.startswith("local_") else None
                if st == "asking" and prev != "asking" and not (e.get("ask") or {}).get("weak"):
                    q = e.get("ask") or {}
                    ops = " / ".join(q.get("options") or [])
                    self.queue("❓ 질문 · " + self.name(k), first_line(q.get("text"), 160) + (f"\n선택지: {ops}" if ops else ""), sid)
                elif st == "idle" and prev in ("working", "waiting_tool") and e.get("label") == "턴 끝남" \
                        and now - self.work_since.get(k, now) >= TURN_MIN_WORK:
                    self.queue("✅ 턴 끝남 · " + self.name(k), first_line(e.get("text")) or "응답을 마쳤어요", sid)
                elif st == "waiting_tool" and prev != "waiting_tool":
                    self.queue("⏸ 멈춘 것 같아요 · " + self.name(k), e.get("label", ""), sid)
            if st == "idle":
                self.work_since.pop(k, None)
            self.prev[k] = st
        for e in edges:
            if e.get("state") != "unread" or e.get("age", 0) < UNREAD_ALERT:
                continue
            key = (e.get("from"), e.get("to"), e.get("kind"), e.get("at"))
            if key in self.seen_edges:
                continue
            self.seen_edges.add(key)
            if not quiet:
                self.queue(f"🧷 요청 {e['age'] // 60}분째 미처리",
                           f"{self.name(e.get('from'))} → {self.name(e.get('to'))}: {first_line(e.get('summary'), 80)}", e.get("to"))
        cur = {r["_sid"] for r in waiting if r.get("_sid")}
        if self.seen_wait is not None and not quiet:
            for r in waiting:
                if r.get("_sid") and r["_sid"] not in self.seen_wait and (live.get(r["_sid"]) or {}).get("state") != "asking":
                    self.queue("⏳ 내 답 대기 · " + r.get("session", ""), first_line(r.get("todo")), r["_sid"])
        self.seen_wait = cur
        self.first = False

    # ── 동작 ──
    def open_board(self, sid=None):
        if time.time() - self.last_open < 2:  # 창이 뜨기 전에 또 눌러도 하나만
            return
        self.last_open = time.time()
        if not self.ok and not self.ensure_server():
            # 빈 「연결할 수 없음」 창 대신 이유를 알린다. 알림을 누르면 로그 파일이 열린다
            self.show_toast("상황판 서버를 못 띄웠어요", f"{self.status}\n누르면 로그(tray.log)가 열려요", "__log__")
            return
        hwnd = find_board_window()
        if hwnd:
            if IsIconic(hwnd):
                ShowWindow(hwnd, 9)  # SW_RESTORE
            SetForegroundWindow(hwnd)
            return
        url = BASE + "/session-board.html" + (f"#s={sid}" if sid else "")
        edge = edge_path()
        try:
            if edge:
                subprocess.Popen([edge, f"--app={url}"], creationflags=DETACHED_PROCESS, close_fds=True)
            else:
                os.startfile(url)
        except OSError as e:
            log("open:", e)

    def menu(self):
        m = CreatePopupMenu()
        AppendMenuW(m, MF_STRING, ID_OPEN, "상황판 열기")
        SetMenuDefaultItem(m, ID_OPEN, 0)
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING | (MF_CHECKED if self.settings.get("notify", True) else 0), ID_NOTIFY, "알림 받기")
        AppendMenuW(m, MF_STRING, ID_TEST, "알림 시험 보내기")
        AppendMenuW(m, MF_STRING | (MF_CHECKED if autostart_on() else 0), ID_AUTOSTART, "로그인 시 자동 실행")
        if VERSION:  # exe 로 돌 때만
            AppendMenuW(m, MF_STRING | (MF_CHECKED if self.settings.get("autoupdate", True) else 0), ID_AUTOUPD, "자동 업데이트(6시간마다 확인)")
            AppendMenuW(m, MF_STRING, ID_UPDCHK, f"지금 업데이트 확인 (지금 v{VERSION})")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        srv = (f"서버: 이 트레이가 띄움 (포트 {PORT})" if self.own_server else
               (f"서버: 다른 곳에서 띄움 (포트 {PORT})" if self.ok else "서버: 꺼짐 — " + self.status[:80]))
        AppendMenuW(m, MF_STRING | MF_GRAYED, 0, srv)
        AppendMenuW(m, MF_STRING | MF_GRAYED, 0, "관제 세션 대기: " + ("연결됨" if self.armed else "아님 — 채팅 릴레이 멈춤"))
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, ID_QUIT, "종료" + (" (서버도 같이 꺼짐)" if self.own_server else ""))
        pt = W.POINT()
        GetCursorPos(ctypes.byref(pt))
        SetForegroundWindow(self.hwnd)
        cmd = TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, self.hwnd, None)
        DestroyMenu(m)
        PostMessageW(self.hwnd, 0, 0, 0)
        if cmd == ID_OPEN:
            self.open_board()
        elif cmd == ID_NOTIFY:
            self.settings["notify"] = not self.settings.get("notify", True)
            save_settings(self.settings)
            if not self.settings["notify"]:
                self.toasts.clear()
        elif cmd == ID_TEST:
            self.test_toast()
        elif cmd == ID_AUTOUPD:
            self.settings["autoupdate"] = not self.settings.get("autoupdate", True)
            save_settings(self.settings)
        elif cmd == ID_UPDCHK:
            threading.Thread(target=self.check_update, args=(True,), daemon=True, name="update-check").start()
        elif cmd == ID_AUTOSTART:
            on = not autostart_on()
            try:
                set_autostart(on)
                self.settings["autostart"] = on
                save_settings(self.settings)
            except Exception as e:
                log("autostart:", e)
        elif cmd == ID_QUIT:
            self.quit()

    def quit(self):
        Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid(0)))
        if self.httpd:
            threading.Thread(target=self.httpd.shutdown, daemon=True).start()
        PostQuitMessage(0)

    def wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == CB_MSG:
                # NOTIFYICON_VERSION_4: LOWORD(lParam) = 이벤트. 왼쪽 클릭은 NIN_SELECT, 오른쪽은 WM_CONTEXTMENU 로 온다
                # (WM_LBUTTONUP·WM_RBUTTONUP 도 같이 오므로 그건 무시해야 두 번 열리지 않는다)
                ev = lp & 0xFFFF
                if ev in (NIN_SELECT, NIN_KEYSELECT):
                    self.open_board()
                elif ev == WM_CONTEXTMENU:
                    self.menu()
                elif ev == NIN_BALLOONUSERCLICK:
                    if self.toast_sid == "__log__":
                        try:
                            os.startfile(str(LOG))
                        except OSError as e:
                            log("open log:", e)
                    else:
                        self.open_board(self.toast_sid)
                return 0
            if msg == WM_TIMER:
                if wp == 2:  # 막대 애니메이션 한 칸
                    self.frame = (self.frame + 1) % len(FRAMES)
                    self.paint_icon()
                else:
                    self.tick()
                return 0
            if msg == self.taskbar_msg and self.taskbar_msg:  # 탐색기가 다시 뜨면 아이콘을 다시 붙인다
                self.add_icon()
                self.icon_key = None
                self.paint_icon()
                return 0
            if msg == 0x0010:  # WM_CLOSE: exe 설치 · 업데이트가 보낸다. 아이콘을 지우고 서버까지 끄고 끝낸다
                self.quit()
                return 0
            if msg == WM_TEST_TOAST:
                self.test_toast()
                return 0
            if msg == WM_UPDATE_CHECK:
                threading.Thread(target=self.check_update, args=(True,), daemon=True, name="update-check").start()
                return 0
            if msg == WM_DESTROY:
                PostQuitMessage(0)
                return 0
        except Exception as e:
            log("wndproc:", repr(e))
        return DefWindowProcW(hwnd, msg, wp, lp)

    def run(self):
        hinst = GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "SessionOrchestratorTray"
        RegisterClassExW(ctypes.byref(wc))
        self.hwnd = CreateWindowExW(0, wc.lpszClassName, TITLE, 0, 0, 0, 0, 0, None, None, hinst, None)
        self.hicon = make_icon(self.size, 0, True, "")
        self.add_icon()
        if "autostart" not in self.settings:  # 처음 한 번은 자동 실행을 켠다(작업자가 고른 트레이 방식의 전제)
            try:
                set_autostart(True)
                self.settings["autostart"] = True
                save_settings(self.settings)
            except Exception as e:
                log("autostart:", e)
        self.tick()
        SetTimer(self.hwnd, 1, POLL_MS, None)
        self.announce_update()
        if VERSION:  # exe 로 돌 때만 자동 업데이트
            threading.Thread(target=self.update_loop, daemon=True, name="update").start()
        msg = W.MSG()
        while GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            TranslateMessage(ctypes.byref(msg))
            DispatchMessageW(ctypes.byref(msg))
        log("종료")


def install():
    """런처 바로가기 + 로그인 자동 실행을 만들고 explorer.exe 로 트레이를 띄운다(Claude 앱 프로세스 트리 밖에서 돌게)."""
    # 바로가기를 못 만들어도 설치는 멈추지 않는다. 이유는 출력한다
    ROOT.mkdir(exist_ok=True)
    try:
        write_ico(ICON)
    except OSError as e:
        print("[주의] 아이콘 파일을 못 만들었습니다(기본 아이콘으로 보임):", e)
    launcher = ROOT / LNK_NAME
    try:
        fixed = make_lnk(launcher, pythonw(), tray_lnk_args(), str(WS), "Session Orchestrator 트레이")
        print("런처:", launcher, f"([주의] 원래 속성 {fixed} 을 풀고 덮어씀)" if fixed else "")
    except Exception as e:
        print("[주의] 런처 바로가기를 못 만들어 트레이를 직접 띄웁니다:", e)
        launcher = None
    try:
        set_autostart(True)
        print("자동 실행:", startup_lnk())
        s = load_settings()
        s["autostart"] = True
        save_settings(s)
    except Exception as e:
        print("[주의] 로그인 자동 실행을 못 켰습니다(트레이 메뉴에서 다시 켤 수 있음):", e)
    if launcher:
        subprocess.Popen(["explorer.exe", str(launcher)], close_fds=True)
    else:  # 런처 없이: 이 콘솔과 떨어진 프로세스로 바로 띄운다
        subprocess.Popen([pythonw()] + (["tray"] if FROZEN else ["-I", str(HERE / "board-tray.py")]), cwd=str(WS), close_fds=True,
                         creationflags=DETACHED_PROCESS | 0x00000200)  # CREATE_NEW_PROCESS_GROUP
    print("트레이를 띄웠습니다(이미 떠 있으면 그대로).")


def main():
    if "--install" in sys.argv:
        return install()
    if "--make-icon" in sys.argv:
        write_ico(ICON)
        return print("아이콘:", ICON)
    if "--uninstall" in sys.argv:
        set_autostart(False)
        s = load_settings()
        s["autostart"] = False
        save_settings(s)
        return print("로그인 자동 실행을 껐습니다:", startup_lnk())
    ROOT.mkdir(exist_ok=True)
    CreateMutexW(None, False, "Local\\SessionOrchestratorTray")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return
    if sys.stdout is None or sys.stderr is None:  # pythonw: 서버 접근 로그·트래커 오류가 갈 곳
        try:
            make_writable(ROOT / "tray-server.log")
            f = open(ROOT / "tray-server.log", "a", encoding="utf-8", buffering=1)
        except OSError:  # 로그 파일을 못 열어도 트레이는 뜬다
            f = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        pass
    log("시작", sys.executable, f"v{VERSION}" if VERSION else "")
    register_app_identity()
    try:
        Tray().run()
    except Exception as e:
        log("치명:", repr(e))
        raise


if __name__ == "__main__":
    main()
