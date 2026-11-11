"""so_app.py — Session Orchestrator 실행 파일(exe) 시작점 (PyInstaller 로 SessionOrchestrator.exe 하나로 묶는다).

  SessionOrchestrator.exe            설치 폴더 밖(받은 폴더 등)에서 실행 = 설치 · 업데이트
                                     설치 폴더(%USERPROFILE%\\SessionOrchestrator)에서 실행 = 트레이 시작(떠 있으면 상황판 열기)
  SessionOrchestrator.exe tray       트레이(로그인 자동 실행이 이걸 부른다)
  SessionOrchestrator.exe render|map|queue|server …   /board 스킬이 쓰는 도구(Python 없이)
  SessionOrchestrator.exe uninstall [--keep-data]     제거(판정 데이터를 남기려면 --keep-data)
  SessionOrchestrator.exe notify-test | update-check   떠 있는 트레이에 시험 알림 · 업데이트 확인을 시킨다
  SessionOrchestrator.exe --update   (트레이의 자동 업데이트가 부른다) 창 없이 설치 · 업데이트

Python 을 따로 깔 필요가 없다. 판정 데이터 · 설정 · 로그는 %USERPROFILE%\\SessionOrchestrator\\board 에 두고,
코드는 exe 안(scripts/)에 들어 있다. 기존 install.cmd 설치와 같은 폴더를 쓰므로 판정 데이터를 그대로 이어받는다.
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
VERSION = "1.2.0"  # 릴리스 태그(vX.Y.Z)와 같아야 한다 — 빌드가 다르면 실패시킨다
HOME = Path(os.environ.get("USERPROFILE") or Path.home()) / APP
EXE = HOME / (APP + ".exe")
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # exe 안에 풀린 코드 위치
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
         '`explorer.exe "' + str(EXE) + '"`(설치 폴더의 exe 를 다시 실행하면 트레이가 뜬다)'),
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


def install(quiet: bool = False) -> int:
    """받은 exe 를 설치 폴더로 복사하고 설정 · 상황판 페이지 · /board 스킬을 만든 뒤 트레이를 띄운다(업데이트도 같은 길).
    quiet = 트레이의 자동 업데이트가 부른 경우. 창을 띄우지 않고(작업 폴더가 없으면 그냥 멈춤), 새 트레이가 알림으로 알린다."""
    def say(text: str, warn: bool = False) -> None:
        if not quiet:
            msgbox(text, warn)
    board = HOME / "board"
    board.mkdir(parents=True, exist_ok=True)
    stop_tray()
    me = Path(sys.executable).resolve()
    # 지난 업데이트 때 비켜 둔 exe 와 내려받은 옛 설치 파일(이제 안 쓰이면 지워진다)
    for old in list(HOME.glob(APP + ".exe.old-*")) + [p for p in (HOME / "update").glob("*") if p.resolve() != me]:
        try:
            old.unlink()
        except OSError:
            pass
    make_writable(EXE)
    for i in range(10):
        try:
            shutil.copy2(me, EXE)
            break
        except OSError as e:
            # 관제 세션의 「queue wait」 같은 다른 exe 가 아직 돌면 파일이 잠겨 덮어쓸 수 없다.
            # 실행 중인 exe 도 이름은 바꿀 수 있으므로 옆으로 비켜 두고 새로 놓는다(그 프로세스는 옛 코드로 마저 돈다)
            if i == 3 and EXE.exists():
                try:
                    EXE.rename(EXE.with_name(EXE.name + ".old-" + time.strftime("%Y%m%d-%H%M%S")))
                except OSError:
                    pass
            if i == 9:
                say(f"설치 폴더에 복사하지 못했습니다.\n{EXE}\n{e}", warn=True)
                if quiet:  # 자동 업데이트가 실패해도 옛 트레이는 다시 띄운다
                    subprocess.Popen([str(EXE), "tray"], cwd=str(HOME), creationflags=DETACHED, close_fds=True)
                return 1
            time.sleep(0.5)
    cfg = read_config()
    ws = cfg.get("workspace") or ""
    if not ws or not Path(ws).is_dir():
        if quiet:
            return 1
        ws = ask_folder()
        if not ws:
            msgbox("작업 폴더를 고르지 않아 설치를 멈췄습니다. 다시 실행하면 이어서 합니다.", warn=True)
            return 1
    cfg["workspace"] = ws
    write_text(board / "config.json", json.dumps(cfg, ensure_ascii=False, indent=1))
    rows = board / "board-sessions.json"
    if not rows.exists():
        rows.write_text("[]", encoding="utf-8")
    tmp = board / "session-board.new.html"
    if run_tool("render", [str(rows), str(tmp)]) == 0 and tmp.exists():
        make_writable(board / "session-board.html")
        os.replace(tmp, board / "session-board.html")
    skill_dir = Path.home() / ".claude" / "skills" / "board"
    skill_dir.mkdir(parents=True, exist_ok=True)
    write_text(skill_dir / "SKILL.md", render_skill(ws))
    remove_old_startup_lnk()
    # 로그인 자동 실행은 레지스트리 Run(트레이와 같은 값). 옛 설정에 autostart 가 이미 있으면 트레이가 다시 켜지 않으므로 여기서 건다
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, f'"{EXE}" tray')
    except OSError:
        pass
    if quiet:  # 새 트레이가 뜨면서 「업데이트했어요」 알림을 띄우게 남겨 둔다
        try:
            (board / "updated.txt").write_text(VERSION, encoding="utf-8")
        except OSError:
            pass
    subprocess.Popen([str(EXE), "tray"], cwd=str(HOME), creationflags=DETACHED, close_fds=True)
    if quiet:
        return 0
    msgbox("설치했습니다.\n\n"
           f"설치 폴더: {HOME}\n감시 작업 폴더: {ws}\n\n"
           "1) 시계 옆 트레이의 막대 아이콘을 누르면 상황판이 열립니다.\n"
           "2) Claude 앱에서 위 작업 폴더로 세션을 열어 사이드바에 고정하고 /board 를 보내면 판정 · 카드 채팅이 켜집니다.\n\n"
           "새 버전은 새 exe 를 받아 실행하면 그대로 업데이트됩니다.")
    return 0


def uninstall(keep_data: bool) -> int:
    """트레이 종료 · 로그인 자동 실행 · /board 스킬 · 설치 폴더를 지운다. 판정 데이터(board)는 keep_data 면 남긴다.
    지금 실행 중인 exe 자신은 지울 수 없어 남기고, 그 경우 폴더를 직접 지우라고 알린다(스스로 지우는 동작은 넣지 않는다)."""
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
    me = Path(sys.executable).resolve()
    left = []
    if HOME.exists():
        for p in HOME.iterdir():
            if (keep_data and p.name == "board") or p.resolve() == me:
                continue
            make_writable(p)
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            except OSError:
                left.append(p.name)
    note = ""
    if me.parent == HOME.resolve():
        note = f"\n\n지금 실행 중인 {me.name} 는 지울 수 없어 남겼습니다. 이 창을 닫은 뒤 {HOME} 폴더를 직접 지워 주세요."
    elif not left and not keep_data:
        try:
            HOME.rmdir()
        except OSError:
            pass
    msgbox("Session Orchestrator 를 제거했습니다(트레이 · 로그인 자동 실행 · /board 스킬 · 설치 파일)."
           + ("\n판정 데이터(board)는 남겼습니다." if keep_data else "")
           + (f"\n지우지 못한 항목: {', '.join(left)}" if left else "") + note)
    return 0


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
    if args and args[0] in ("notify-test", "update-check"):  # 떠 있는 트레이에 시키기(알림 모양 · 업데이트 확인)
        h = tray_window()
        if not h:
            print("트레이가 떠 있지 않습니다")
            return 1
        ctypes.windll.user32.PostMessageW(h, 0x8000 + (0x30 if args[0] == "notify-test" else 0x31), 0, 0)
        print("트레이에 보냈습니다:", args[0])
        return 0
    if args and args[0] == "--update":
        return install(quiet=True)
    if Path(sys.executable).resolve() == EXE.resolve() and read_config().get("workspace"):
        if tray_window():  # 이미 떠 있으면 아무것도 안 함(트레이 아이콘으로 연다)
            return 0
        return run_tool("tray", [])
    return install()


if __name__ == "__main__":
    sys.exit(main())
