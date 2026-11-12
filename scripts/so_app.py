"""so_app.py — Session Orchestrator 실행 파일(exe) 시작점. PyInstaller 폴더형(onedir)으로 묶어 zip 으로 배포한다.

  SessionOrchestrator.exe            처음 = 작업 폴더를 고르고 설정 · 상황판 페이지 · /board 스킬을 만든 뒤 트레이 시작
                                     다음부터 = 트레이 시작(이 exe 의 트레이가 이미 떠 있으면 상황판 열기)
                                     다른 exe(옛 버전 · 옮기기 전 폴더)의 트레이가 떠 있으면 닫고 이 exe 로 바꿔 띄운다
  SessionOrchestrator.exe tray       트레이(로그인 자동 실행이 이걸 부른다)
  SessionOrchestrator.exe render|map|queue|server …   /board 스킬이 쓰는 도구(Python 없이)
  SessionOrchestrator.exe uninstall [--keep-data]     제거(판정 데이터를 남기려면 --keep-data)
  SessionOrchestrator.exe notify-test | update-check | open   떠 있는 트레이에 시험 알림 · 새 버전 확인 · 상황판 열기를 시킨다

압축을 푼 그 자리에서 돈다. exe 가 자기 자신을 다른 곳으로 복사하거나, 인터넷에서 받은 파일을 실행하지 않는다
(v1.2.0 까지의 「단일 exe 가 스스로 설치 폴더로 복사 + 새 exe 를 받아 실행」은 보안 프로그램(백신)이 악성 설치기로
보고 지웠다). 새 버전 = 새 zip 을 풀어 그 안의 exe 를 한 번 실행. 폴더를 옮겼을 때도 옮긴 곳의 exe 를 한 번 실행한다.
판정 데이터 · 설정 · 로그는 그대로 %USERPROFILE%\\SessionOrchestrator\\board 에 두어 옛 버전의 데이터를 이어받는다.
"""
# PyInstaller 가 함께 넣도록, exe 안의 스크립트들이 쓰는 표준 모듈을 여기서 미리 불러 둔다
import argparse  # noqa: F401
import collections  # noqa: F401
import ctypes
import ctypes.wintypes  # noqa: F401
import datetime  # noqa: F401
import email.utils  # noqa: F401
import glob  # noqa: F401
import hashlib  # noqa: F401
import html  # noqa: F401
import http.server  # noqa: F401
import importlib.util  # noqa: F401
import json
import mimetypes  # noqa: F401
import msvcrt
import os
import re  # noqa: F401
import runpy
import secrets  # noqa: F401
import shutil
import socket  # noqa: F401
import socketserver  # noqa: F401
import ssl  # noqa: F401  (자동 업데이트가 GitHub 에 https 로 묻는다)
import struct  # noqa: F401
import subprocess
import sys
import threading  # noqa: F401
import time
import urllib.parse  # noqa: F401
import urllib.request  # noqa: F401
import winreg  # noqa: F401
import zlib  # noqa: F401
from pathlib import Path

APP = "SessionOrchestrator"
TITLE = "Session Orchestrator"
VERSION = "1.3.0"  # 릴리스 태그(vX.Y.Z)와 같아야 한다 — 빌드가 다르면 실패시킨다
HOME = Path(os.environ.get("USERPROFILE") or Path.home()) / APP  # 데이터 폴더(board/)
FROZEN = bool(getattr(sys, "frozen", False))
EXE = Path(sys.executable).resolve() if FROZEN else HOME / (APP + ".exe")  # 지금 도는 exe(압축 푼 자리)
LEGACY_EXE = HOME / (APP + ".exe")  # v1.2.0 까지 단일 exe 가 스스로 복사해 두던 자리
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # exe 옆 _internal(코드 위치)
SCRIPTS = BUNDLE / "scripts"
TOOLS = {"tray": "board-tray.py", "render": "board-render.py", "map": "board-map.py",
         "queue": "board-queue.py", "server": "board-server.py"}
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def msgbox(text: str, warn: bool = False) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x30 if warn else 0x40)


def attach_std() -> None:
    """창 없는 exe 는 sys.stdout 이 비어 있다. 부모(예: Claude 의 Bash)가 파이프를 넘겼으면 그걸 붙여 출력이 보이게 한다."""
    k = ctypes.windll.kernel32
    k.GetStdHandle.restype = ctypes.c_void_p
    for num, name in ((-11, "stdout"), (-12, "stderr")):
        if getattr(sys, name) is not None:
            continue
        h = k.GetStdHandle(num)
        if not h or h == ctypes.c_void_p(-1).value:
            continue
        try:
            fd = msvcrt.open_osfhandle(h, os.O_WRONLY)
            setattr(sys, name, open(fd, "w", encoding="utf-8", buffering=1))
        except OSError:
            pass


def run_tool(name: str, args: list) -> int:
    os.environ["SO_HOME"] = str(HOME)
    os.environ["SO_VERSION"] = VERSION  # 트레이의 자동 업데이트가 지금 버전을 안다
    sys.path.insert(0, str(SCRIPTS))
    sys.argv = [str(SCRIPTS / TOOLS[name])] + list(args)
    try:
        runpy.run_path(str(SCRIPTS / TOOLS[name]), run_name="__main__")
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


def tray_window():
    return ctypes.windll.user32.FindWindowW("SessionOrchestratorTray", None)


def tray_exe(h) -> Path | None:
    """떠 있는 트레이 창을 가진 프로세스의 실행 파일(옛 Python 트레이면 pythonw.exe)."""
    k32 = ctypes.windll.kernel32
    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    k32.OpenProcess.restype = ctypes.c_void_p
    hp = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not hp:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(ctypes.c_void_p(hp), 0, buf, ctypes.byref(n)):
            return Path(buf.value)
    finally:
        k32.CloseHandle(ctypes.c_void_p(hp))
    return None


def same_file(a: Path | None, b: Path) -> bool:
    try:
        return a is not None and os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return False


def stop_tray() -> None:
    """떠 있는 트레이(옛 Python 트레이 포함)를 창 메시지로 닫는다. PowerShell 을 띄우지 않는다."""
    h = tray_window()
    if not h:
        return
    ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
    for _ in range(50):
        time.sleep(0.1)
        if not tray_window():
            break
    time.sleep(0.5)  # 프로세스가 exe 파일을 놓을 때까지


def make_writable(p: Path) -> None:
    try:
        if p.exists():
            ctypes.windll.kernel32.SetFileAttributesW(str(p), 0x80)
    except Exception:
        pass


def write_text(p: Path, text: str) -> None:
    make_writable(p)
    try:
        p.write_text(text, encoding="utf-8")
    except OSError:  # 덮어쓰기가 막히면 옛 파일을 비켜 두고 새로
        p.rename(p.with_name(p.name + ".old-" + time.strftime("%Y%m%d-%H%M%S")))
        p.write_text(text, encoding="utf-8")


def read_config() -> dict:
    try:
        d = json.loads((HOME / "board" / "config.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


class BROWSEINFOW(ctypes.Structure):
    _fields_ = [("hwndOwner", ctypes.wintypes.HWND), ("pidlRoot", ctypes.c_void_p), ("pszDisplayName", ctypes.wintypes.LPWSTR),
                ("lpszTitle", ctypes.wintypes.LPCWSTR), ("ulFlags", ctypes.wintypes.UINT), ("lpfn", ctypes.c_void_p),
                ("lParam", ctypes.wintypes.LPARAM), ("iImage", ctypes.c_int)]


def ask_folder() -> str:
    """Windows 기본 폴더 고르기 창. tkinter 를 쓰지 않는다 — tkinter 를 넣으면 exe 가 실행마다 파일 900여 개를 더 풀어
    보안 프로그램(백신) 검사로 한 번 실행에 40초가 걸렸다(11-11 실측)."""
    msgbox("Claude Code 세션을 여는 작업 폴더를 골라 주세요.\n그 폴더(와 그 아래)에서 연 세션이 상황판에 보입니다.")
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    ole32.CoInitialize(None)
    name = ctypes.create_unicode_buffer(260)
    # BIF_RETURNONLYFSDIRS | BIF_EDITBOX | BIF_NEWDIALOGSTYLE
    bi = BROWSEINFOW(None, None, ctypes.cast(name, ctypes.wintypes.LPWSTR), "감시할 작업 폴더(Claude Code 세션을 여는 폴더)를 고르세요", 0x0001 | 0x0010 | 0x0040, None, 0, 0)
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
    if not pidl:
        return ""
    path = ctypes.create_unicode_buffer(1024)
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, ctypes.wintypes.LPWSTR]
    ok = shell32.SHGetPathFromIDListW(pidl, path)
    ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
    return path.value if ok and path.value else ""


def render_skill(workspace: str) -> str:
    tpl = (BUNDLE / "skill" / "SKILL.md").read_text(encoding="utf-8")
    exe_fwd = str(EXE).replace("\\", "/")
    reps = [
        ('| Python | `"{{PY}}"` |', '| 실행 파일 | `"{{EXE}}"` (Python 필요 없음) |'),
        ('아래에서 `PY` = `"{{PY}}"`', '아래에서 `EXE` = `"{{EXE}}"`'),
        ('`explorer.exe "H/board/Session Orchestrator.lnk"`(경로는 백슬래시로) 또는 `PY -I H/scripts/board-tray.py --install`',
         '`explorer.exe "' + str(EXE) + '"`(이 exe 를 다시 실행하면 트레이가 뜬다)'),
        ("PY -I H/scripts/board-map.py", "EXE map"),
        ("PY -I H/scripts/board-render.py", "EXE render"),
        ("PY -I H/scripts/board-queue.py", "EXE queue"),
    ]
    for a, b in reps:
        tpl = tpl.replace(a, b)
    return (tpl.replace("{{EXE}}", exe_fwd).replace("{{HOME}}", str(HOME).replace("\\", "/"))
            .replace("{{WORKSPACE}}", workspace).replace("{{PY}}", exe_fwd))


def remove_old_startup_lnk() -> None:
    """install.cmd 로 깐 Python 트레이의 시작프로그램 바로가기(이 설치 폴더를 가리킬 때만)를 지운다. exe 는 레지스트리 Run 을 쓴다."""
    lnk = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / (TITLE + ".lnk")
    try:
        if lnk.exists() and str(HOME / "scripts" / "board-tray.py").encode("utf-16-le") in lnk.read_bytes():
            make_writable(lnk)
            lnk.unlink()
    except OSError:
        pass


def write_if_changed(p: Path, text: str) -> None:
    try:
        if p.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    write_text(p, text)


def remove_legacy() -> None:
    """v1.2.0 까지의 흔적: 설치 폴더로 복사된 단일 exe · 업데이트 때 비켜 둔 exe · 자동 업데이트가 받아 둔 exe."""
    olds = list(HOME.glob(APP + ".exe.old-*")) + list((HOME / "update").glob("*"))
    if not same_file(LEGACY_EXE, EXE):  # zip 을 데이터 폴더 자리에 풀었으면 그 exe 가 지금 도는 것 — 남긴다
        olds.append(LEGACY_EXE)
    for old in olds:
        try:
            if old.exists():
                make_writable(old)
                old.unlink()
        except OSError:
            pass
    try:
        (HOME / "update").rmdir()
    except OSError:
        pass


def setup() -> int:
    """설정 · 상황판 페이지 · /board 스킬을 이 exe 기준으로 맞추고 트레이를 띄운다. 처음 실행 · 새 버전 · 폴더 옮김 모두 같은 길.
    파일을 복사하지 않는다 — 압축을 푼 그 자리의 exe 가 돈다. 로그인 자동 실행 값은 트레이가 자기 exe 로 맞춘다."""
    board = HOME / "board"
    board.mkdir(parents=True, exist_ok=True)
    stop_tray()  # 다른 exe(옛 버전 · 옮기기 전 폴더 · Python 트레이)의 트레이
    remove_legacy()
    cfg = read_config()
    first = not cfg.get("workspace")
    ws = cfg.get("workspace") or ""
    if not ws or not Path(ws).is_dir():
        ws = ask_folder()
        if not ws:
            msgbox("작업 폴더를 고르지 않아 멈췄습니다. 다시 실행하면 이어서 합니다.", warn=True)
            return 1
    changed = not first and cfg.get("version") != VERSION  # 옛 버전(v1.2.0 은 version 값이 없음)에서 바뀜
    cfg["workspace"], cfg["version"] = ws, VERSION
    try:
        write_if_changed(board / "config.json", json.dumps(cfg, ensure_ascii=False, indent=1))
        rows = board / "board-sessions.json"
        if not rows.exists():
            rows.write_text("[]", encoding="utf-8")
        tmp = board / "session-board.new.html"
        if run_tool("render", [str(rows), str(tmp)]) == 0 and tmp.exists():
            make_writable(board / "session-board.html")
            os.replace(tmp, board / "session-board.html")
        skill_dir = Path.home() / ".claude" / "skills" / "board"
        skill_dir.mkdir(parents=True, exist_ok=True)
        write_if_changed(skill_dir / "SKILL.md", render_skill(ws))
    except OSError as e:
        msgbox(f"설정 파일을 쓰지 못했습니다.\n{e}", warn=True)
        return 1
    remove_old_startup_lnk()
    if changed:  # 새 트레이가 뜨면서 「새 버전으로 바뀌었어요」 알림을 띄우게 남겨 둔다
        try:
            (board / "updated.txt").write_text(VERSION, encoding="utf-8")
        except OSError:
            pass
    subprocess.Popen([str(EXE), "tray"], cwd=str(HOME), creationflags=DETACHED, close_fds=True)
    if first:
        msgbox("준비했습니다.\n\n"
               f"프로그램 폴더: {EXE.parent}\n데이터 폴더: {HOME}\n감시 작업 폴더: {ws}\n\n"
               "1) 시계 옆 트레이의 막대 아이콘을 누르면 상황판이 열립니다.\n"
               "2) Claude 앱에서 위 작업 폴더로 세션을 열어 사이드바에 고정하고 /board 를 보내면 판정 · 카드 채팅이 켜집니다.\n\n"
               "프로그램 폴더를 옮기면 옮긴 곳의 exe 를 한 번 실행해 주세요. 새 버전은 새 zip 을 풀어 그 안의 exe 를 실행하면 바뀝니다.")
    return 0


def uninstall(keep_data: bool) -> int:
    """트레이 종료 · 로그인 자동 실행 · /board 스킬 · 데이터 폴더를 지운다. 판정 데이터(board)는 keep_data 면 남긴다.
    압축을 푼 프로그램 폴더(지금 도는 exe 가 든 곳)는 스스로 지우지 않고, 직접 지우라고 알린다."""
    stop_tray()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP)
    except OSError:
        pass
    remove_old_startup_lnk()
    skill = Path.home() / ".claude" / "skills" / "board" / "SKILL.md"
    try:
        if skill.exists() and str(HOME).replace("\\", "/") in skill.read_text(encoding="utf-8"):
            shutil.rmtree(skill.parent, ignore_errors=True)
    except OSError:
        pass
    mine = {os.path.normcase(str(EXE)), os.path.normcase(str(EXE.parent / "_internal"))}  # 지금 도는 프로그램(데이터 폴더에 풀었을 때)
    left = []
    if HOME.exists():
        for p in HOME.iterdir():
            if (keep_data and p.name == "board") or os.path.normcase(str(p.resolve())) in mine:
                continue
            make_writable(p)
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            except OSError:
                left.append(p.name)
        if not left and not keep_data:
            try:
                HOME.rmdir()
            except OSError:
                pass
    msgbox("Session Orchestrator 를 제거했습니다(트레이 · 로그인 자동 실행 · /board 스킬 · 데이터)."
           + ("\n판정 데이터(board)는 남겼습니다." if keep_data else "")
           + (f"\n지우지 못한 항목: {', '.join(left)}" if left else "")
           + f"\n\n이 창을 닫은 뒤 프로그램 폴더를 직접 지워 주세요:\n{EXE.parent}")
    return 0


TRAY_ORDERS = {"notify-test": 0x30, "update-check": 0x31, "open": 0x32}  # 트레이의 WM_APP + 값


def main() -> int:
    attach_std()
    args = sys.argv[1:]
    if args and args[0] in TOOLS:
        return run_tool(args[0], args[1:])
    if args and args[0] in ("--version", "version"):
        print(APP, VERSION)
        return 0
    if args and args[0] == "uninstall":
        return uninstall(keep_data="--keep-data" in args)
    if args and args[0] in TRAY_ORDERS:  # 떠 있는 트레이에 시키기(알림 모양 · 새 버전 확인 · 상황판 열기)
        h = tray_window()
        if not h:
            print("트레이가 떠 있지 않습니다")
            return 1
        ctypes.windll.user32.PostMessageW(h, 0x8000 + TRAY_ORDERS[args[0]], 0, 0)
        print("트레이에 보냈습니다:", args[0])
        return 0
    h = tray_window()
    if h and same_file(tray_exe(h), EXE) and read_config().get("workspace"):
        ctypes.windll.user32.PostMessageW(h, 0x8000 + TRAY_ORDERS["open"], 0, 0)  # 이미 떠 있으면 상황판만 연다
        return 0
    return setup()


if __name__ == "__main__":
    sys.exit(main())
