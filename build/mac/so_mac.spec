# -*- mode: python -*-
# PyInstaller 설정 — macOS 앱 「Session Orchestrator.app」(애플 실리콘 · 인텔 공용 universal2, 파이썬 내장).
#
#   SO_VERSION=1.2.3 pyinstaller --noconfirm build/mac/so_mac.spec     (macOS 에서, universal2 파이썬 + pyinstaller + pyobjc)
#
# 시작점은 scripts/so_mac.py. 공유 스크립트(scripts/*)와 스킬 템플릿(skill/)은 데이터로 넣고, 앱이 처음 열릴 때
# ~/SessionOrchestrator/scripts 로 복사해 이 앱 안의 파이썬으로 돌린다. 그래서 그 스크립트들이 쓰는 표준 모듈을
# hiddenimports 로 미리 넣어야 한다(PyInstaller 는 시작점이 직접 부르는 모듈만 찾아 넣는다). 스크립트에 import 를 더하면 여기도.
# 릴리스 CI(.github/workflows/build-mac.yml)가 이 파일을 그대로 쓴다.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
VERSION = os.environ.get("SO_VERSION", "0.0.0")
ICON = os.path.join(SPECPATH, "SessionOrchestrator.icns")  # build/mac/make_icns.py 가 만든다

STDLIB = [
    # board-agent · board-server · board_live · board-map · board-queue · board-render 가 쓰는 것
    "argparse", "collections", "datetime", "errno", "fcntl", "glob", "http.server", "importlib.util", "json", "os",
    "pathlib", "plistlib", "re", "secrets", "signal", "socket", "socketserver", "struct", "subprocess", "threading",
    "time", "urllib.parse", "urllib.request", "webbrowser", "zlib", "email.utils", "html", "mimetypes", "runpy",
    "faulthandler",
]
PYOBJC = ["objc", "AppKit", "Foundation", "PyObjCTools"]  # 메뉴 막대 아이콘(board-agent.py run_menubar)

a = Analysis(
    [os.path.join(ROOT, "scripts", "so_mac.py")],
    pathex=[],
    datas=[(os.path.join(ROOT, "scripts"), "scripts"), (os.path.join(ROOT, "skill"), "skill")],
    hiddenimports=STDLIB + PYOBJC,
    excludes=["tkinter", "unittest", "pydoc_data"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [("X utf8_mode=1", None, "OPTION")],  # launchd · Finder 에서 떠도 한글 입출력이 UTF-8
    exclude_binaries=True,
    name="SessionOrchestrator",
    console=False,
    target_arch="universal2",
    codesign_identity=None,   # 임시(ad-hoc) 서명. Apple 개발자 서명이 없어 처음 열 때 「그래도 열기」가 필요하다
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SessionOrchestrator")
app = BUNDLE(
    coll,
    name="Session Orchestrator.app",
    icon=ICON if os.path.exists(ICON) else None,
    bundle_identifier="local.session-orchestrator.app",
    version=VERSION,
    info_plist={
        "CFBundleName": "Session Orchestrator",
        "CFBundleDisplayName": "Session Orchestrator",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSUIElement": True,          # 독 · ⌘Tab 에 안 나오고 메뉴 막대 아이콘으로만 있는다
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    },
)
