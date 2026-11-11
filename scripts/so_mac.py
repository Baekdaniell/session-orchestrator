"""so_mac.py — Session Orchestrator macOS 앱 시작점. PyInstaller 로 「Session Orchestrator.app」(파이썬 내장, 애플 실리콘 · 인텔 공용)으로
묶는다(build/mac/so_mac.spec, .github/workflows/build-mac.yml). 맥에 Python 을 따로 깔 필요가 없다.

  앱을 연다(Finder · Launchpad · Spotlight)
      처음이면 감시할 작업 폴더를 묻고 설치한다. 그다음부터는 떠 있으면 상황판만 열고, 앱을 새 판으로 바꿨거나 옮겼으면
      도구 · /board 스킬 · 로그인 자동 실행을 이 앱에 맞춘 뒤 연다.
  SessionOrchestrator -I <스크립트.py> [인자 …]   파이썬처럼 그 스크립트를 돌린다. /board 스킬(PY = 이 실행 파일)과 LaunchAgent 가 쓴다
  SessionOrchestrator -c <코드>
  SessionOrchestrator setup [--workspace 폴더] [--no-start] [--no-open]   설치 · 업데이트(폴더를 안 주면 기존 값, 없으면 묻는다)
  SessionOrchestrator uninstall [--keep-data]     상주 · 로그인 자동 실행 · /board 스킬 · 설치 폴더 제거(앱 자체는 휴지통으로)
  SessionOrchestrator open | status | version

설치 모양은 install.sh 와 같다 — 도구 ~/SessionOrchestrator/scripts(앱에 든 scripts 를 복사), 데이터 ~/SessionOrchestrator/board,
스킬 ~/.claude/skills/board/SKILL.md, LaunchAgent ~/Library/LaunchAgents/local.session-orchestrator.plist.
그래서 공유 스크립트(board-*.py)를 고치지 않고 그대로 돌리며, install.sh 로 깐 것도 앱이 이어받는다.
메뉴 막대 아이콘은 board-agent.py 가 그린다(앱에 PyObjC 가 들어 있어 켜진다). 기록은 ~/SessionOrchestrator/board/app.log.
시험용 환경변수: SO_INSTALL_DIR · SO_SKILL_ROOT (install.sh 와 같음).
"""
import argparse
import json
import os
import plistlib
import runpy
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TITLE = "Session Orchestrator"
LABEL = "local.session-orchestrator"
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # 앱 안의 scripts · skill (개발 중 실행이면 리포)
HOME = Path(os.environ.get("SO_INSTALL_DIR") or (Path.home() / "SessionOrchestrator"))
SKILL_DIR = Path(os.environ.get("SO_SKILL_ROOT") or (Path.home() / ".claude" / "skills")) / "board"
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
SCRIPTS = HOME / "scripts"
BOARD = HOME / "board"
CFG = BOARD / "config.json"
STAMP = ".so-app-version"            # scripts 폴더 안: 어느 앱 판에서 복사했는지
EXE = sys.executable                  # 앱이면 …/Session Orchestrator.app/Contents/MacOS/SessionOrchestrator
PY_FLAGS = {"-I", "-u", "-B", "-E", "-s", "-S", "-O"}


def utf8_stdio():
    # launchd · Finder 에서 뜨면 LANG 이 없어 글자 인코딩이 ASCII 로 잡힐 수 있다(한글 출력에서 죽지 않게)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def say(*a):
    """터미널에 찍고 board/app.log 에도 남긴다(Finder 에서 연 앱은 터미널이 없어 기록이 유일한 단서)."""
    line = " ".join(str(x) for x in a)
    try:
        print(line, flush=True)
    except Exception:
        pass
    if not BOARD.is_dir():  # 폴더를 만들지는 않는다(제거한 뒤 기록 한 줄 때문에 폴더가 되살아나지 않게)
        return
    try:
        log = BOARD / "app.log"
        if log.exists() and log.stat().st_size > 256_000:
            log.replace(log.with_suffix(".log.1"))
        with open(log, "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def app_version() -> str:
    """앱 번들 Info.plist 의 버전. 개발 중 실행이면 dev."""
    if FROZEN:
        try:
            info = plistlib.loads((Path(EXE).resolve().parent.parent / "Info.plist").read_bytes())
            return str(info.get("CFBundleShortVersionString") or "0.0.0")
        except (OSError, ValueError):
            pass
    return "dev"


def read_cfg() -> dict:
    try:
        d = json.loads(CFG.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def port() -> int:
    try:
        p = int(read_cfg().get("port") or 18799)
        return p if 1024 <= p <= 65535 else 18799
    except (TypeError, ValueError):
        return 18799


# ── 파이썬 흉내 ──
def run_python(argv) -> int:
    """[-I 같은 플래그 …] (스크립트.py | -c 코드) [인자 …] 를 이 앱에 든 파이썬으로 돌린다. 스크립트의 SystemExit 은 그대로 위로 간다."""
    i = 0
    while i < len(argv):
        if argv[i] in PY_FLAGS:
            i += 1
        elif argv[i] in ("-X", "-W"):
            i += 2
        else:
            break
    rest = argv[i:]
    if not rest:
        print("SessionOrchestrator: 돌릴 스크립트가 없습니다(-I <스크립트.py> 또는 -c <코드>)", file=sys.stderr)
        return 2
    if rest[0] == "-c":
        if len(rest) < 2:
            print("SessionOrchestrator: -c 뒤에 코드가 없습니다", file=sys.stderr)
            return 2
        sys.argv = ["-c"] + rest[2:]
        exec(compile(rest[1], "<string>", "exec"), {"__name__": "__main__"})
        return 0
    script = Path(rest[0]).expanduser().resolve()
    if FROZEN and script.name == "board-agent.py" and script.parent == SCRIPTS.resolve():
        try:  # 로그인 때 LaunchAgent 가 상주를 띄우는 길: 앱을 새 판으로 바꿨는데 도구가 옛것이면 먼저 맞춘다
            sync_scripts(quiet=True)
        except OSError as e:
            say("도구 맞추기 실패(옛 도구로 계속):", e)
    sys.argv = [str(script)] + rest[1:]
    sys.path.insert(0, str(script.parent))
    runpy.run_path(str(script), run_name="__main__")
    return 0


# ── 설치 ──
def sync_scripts(quiet=False, force=False) -> bool:
    """앱에 든 scripts 를 ~/SessionOrchestrator/scripts 로 복사한다(새 폴더에 넣고 폴더째 바꿔 끼움). 같은 판이면 건너뛴다."""
    ver = app_version()
    if not force and ver != "dev":
        try:
            if (SCRIPTS / STAMP).read_text(encoding="utf-8").strip() == ver:
                return False
        except OSError:
            pass
    HOME.mkdir(parents=True, exist_ok=True)
    new = HOME / "scripts.new"
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir()
    for f in (BUNDLE / "scripts").iterdir():
        if f.is_file() and f.suffix != ".pyc":
            shutil.copyfile(f, new / f.name)
    (new / STAMP).write_text(ver, encoding="utf-8")
    old = None
    if SCRIPTS.exists():
        old = HOME / ("scripts.old-" + time.strftime("%Y%m%d-%H%M%S"))
        SCRIPTS.rename(old)
    try:
        new.rename(SCRIPTS)
    except OSError:
        if old:
            old.rename(SCRIPTS)
        raise
    for d in HOME.glob("scripts.old-*"):
        shutil.rmtree(d, ignore_errors=True)
    if not quiet:
        say("  도구:", SCRIPTS, f"(앱 {ver})")
    return True


def run_script(name, *args, capture=False):
    """설치된 도구를 이 앱(개발 중이면 이 파이썬)으로 따로 띄워 돌린다."""
    return subprocess.run([EXE, "-I", str(SCRIPTS / name), *map(str, args)],
                          capture_output=capture, text=True, encoding="utf-8", errors="replace")


def osa(lines, *args, timeout=600):
    """AppleScript 를 돌린다. 글은 스크립트에 끼워 넣지 않고 인자(argv)로 넘긴다(따옴표가 있어도 안 깨지게)."""
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    try:
        return subprocess.run(cmd + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        say("osascript 실패:", e)
        return None


def ask_folder() -> str:
    r = osa(["on run argv", "return POSIX path of (choose folder with prompt (item 1 of argv))", "end run"],
            "감시할 작업 폴더를 고르세요 — Claude Code 세션을 여는 폴더")
    return r.stdout.strip() if r is not None and r.returncode == 0 else ""


def dialog(msg: str):
    osa(["on run argv", "display dialog (item 1 of argv) with title (item 2 of argv) buttons {(item 3 of argv)} default button 1",
         "end run"], msg, TITLE, "확인")


def bad_location() -> str:
    """디스크 이미지 안이나 격리 경로(App Translocation)에서 연 앱은 LaunchAgent 가 곧 사라질 경로를 가리키게 된다."""
    if not FROZEN:
        return ""
    p = str(Path(EXE).resolve())
    if "/AppTranslocation/" in p:
        return "내려받은 자리에서 바로 실행됨(macOS 가 임시 경로로 옮겨 돌림)"
    if p.startswith("/Volumes/"):
        return "디스크 이미지(.dmg) 안에서 실행됨"
    return ""


def render_skill(ws: str):
    tpl = (BUNDLE / "skill" / "SKILL.md").read_text(encoding="utf-8")
    text = tpl.replace("{{HOME}}", str(HOME)).replace("{{WORKSPACE}}", ws).replace("{{PY}}", EXE)
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    skill = SKILL_DIR / "SKILL.md"
    if skill.exists() and "Session Orchestrator" not in skill.read_text(encoding="utf-8", errors="replace"):
        bak = skill.with_name("SKILL.md.bak-" + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copyfile(skill, bak)
        say("  [주의] 다른 board 스킬이 있어 백업했습니다:", bak)
    skill.write_text(text, encoding="utf-8")
    say("  /board 스킬:", skill)


def health_url(seconds=20.0) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 시스템 프록시를 거치지 않게
    end = time.time() + seconds
    while True:
        url = f"http://127.0.0.1:{port()}/"   # 상주가 포트를 옮겼을 수 있어 매번 다시 읽는다
        try:
            with opener.open(url + "api/health", timeout=2) as r:
                if b'"ok"' in r.read():
                    return url
        except Exception:
            pass
        if time.time() >= end:
            return ""
        time.sleep(0.5)


def open_url(url: str):
    if sys.platform == "darwin":
        subprocess.run(["open", url])
    else:
        import webbrowser
        webbrowser.open(url)


def plist_python() -> str:
    try:
        return str(plistlib.loads(PLIST.read_bytes()).get("ProgramArguments", [""])[0])
    except (OSError, ValueError, IndexError, AttributeError):
        return ""


def quick_open() -> bool:
    """이미 이 앱으로 설치돼 있고 상주가 떠 있으면 상황판만 연다. 하나라도 어긋나면 False(→ setup 으로 맞춤)."""
    ver = app_version()
    try:
        same = (SCRIPTS / STAMP).read_text(encoding="utf-8").strip() == ver and ver != "dev"
    except OSError:
        same = False
    if not (read_cfg().get("workspace") and same and plist_python() == EXE):
        return False
    url = health_url(3)
    if url:
        open_url(url)
    return bool(url)


def setup(workspace="", start=True, open_=True, interactive=False) -> int:
    where = bad_location()
    if where:
        msg = f"{TITLE} 를 응용 프로그램 폴더로 끌어다 놓은 뒤 거기서 다시 여세요.\n\n({where})"
        say(msg)
        if interactive:
            dialog(msg)
        return 1
    BOARD.mkdir(parents=True, exist_ok=True)
    say(f"{TITLE} 설치: {HOME} (앱 {app_version()}, {EXE})")
    cfg = read_cfg()
    first = not cfg.get("workspace")
    ws = workspace or cfg.get("workspace") or ""
    if not ws and interactive:
        ws = ask_folder()
        if not ws:
            say("작업 폴더를 고르지 않아 그만둡니다")
            return 0
    if not ws:
        say("[실패] 감시할 작업 폴더가 없습니다: setup --workspace <폴더>")
        return 1
    wsp = Path(ws).expanduser()
    if not wsp.is_dir():
        say("[실패] 폴더가 없습니다:", ws)
        if interactive:
            dialog(f"폴더가 없습니다:\n{ws}")
        return 1
    ws = str(wsp.resolve())
    say("  감시 작업 폴더:", ws)
    slug = "".join(c if c.isascii() and c.isalnum() else "-" for c in ws)  # board_live.slug 와 같은 규칙
    if not list((Path.home() / ".claude" / "projects").glob(glob_escape(slug) + "*")):
        say("  [주의] 이 폴더에서 연 Claude Code 세션 기록이 아직 없습니다. 세션을 열면 그때부터 보입니다.")

    sync_scripts(force=True)
    cfg["workspace"] = ws  # 상주가 옮겨 둔 port 는 그대로 둔다
    tmp = CFG.with_name("config.json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CFG)
    render_skill(ws)

    rows, html, tmp_html = BOARD / "board-sessions.json", BOARD / "session-board.html", BOARD / "session-board.new.html"
    if not rows.exists():
        rows.write_text("[]", encoding="utf-8")
    r = run_script("board-render.py", rows, tmp_html, capture=True)
    if r.returncode == 0 and tmp_html.exists():
        tmp_html.replace(html)
    elif html.exists():
        say("  [주의] 상황판 페이지를 새로 만들지 못해 옛 페이지를 그대로 씁니다:", (r.stderr or "").strip()[-200:])
    else:
        say("[실패] 상황판 페이지 만들기 실패:", (r.stderr or "").strip()[-300:])
        if interactive:
            dialog("상황판 페이지를 만들지 못했습니다. 기록: " + str(BOARD / "app.log"))
        return 1
    if not start:
        say("  --no-start: 상주는 띄우지 않았습니다")
        return 0

    r = run_script("board-agent.py", "--install", "--python", EXE, capture=True)
    for line in (r.stdout or "").splitlines() + (r.stderr or "").splitlines():
        say("  " + line)
    url = health_url(30)  # 서명 없는 앱의 첫 실행은 macOS 검사로 늦을 수 있다
    if not url:
        say("[주의] 30초 안에 상황판 서버가 응답하지 않았습니다. 기록:", BOARD / "agent.log", "·", BOARD / "agent-launchd.log")
        if interactive:
            dialog("상황판 서버가 응답하지 않습니다.\n기록 파일을 그대로 전달해 주세요:\n" + str(BOARD / "agent.log"))
        return 1
    say("  상황판 서버 확인:", url)
    if open_:
        open_url(url)
    if first and interactive:
        dialog("설치를 마쳤습니다. 메뉴 막대의 막대 아이콘에서 상황판을 열 수 있습니다.\n\n"
               "Claude 앱에서 이 폴더로 새 세션을 열어 「관제 (상황판)」처럼 이름을 바꿔 고정한 뒤 /board 를 보내면 "
               "내 답 대기 · 완료 판정과 카드 채팅이 켜집니다.")
    return 0


def glob_escape(s: str) -> str:
    return "".join("[" + c + "]" if c in "*?[" else c for c in s)


def uninstall(keep_data=False) -> int:
    say(f"{TITLE} 제거: {HOME}")
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    try:
        args = plistlib.loads(PLIST.read_bytes()).get("ProgramArguments") or []
    except (OSError, ValueError):
        args = []
    if str(SCRIPTS / "board-agent.py") in args:
        PLIST.unlink()
        say("  로그인 자동 실행(LaunchAgent) 삭제")
    skill = SKILL_DIR / "SKILL.md"
    try:
        if str(HOME) in skill.read_text(encoding="utf-8", errors="replace"):
            skill.unlink()
            try:
                SKILL_DIR.rmdir()  # 설치 때 백업해 둔 다른 스킬이 있으면 폴더는 남는다
                say("  /board 스킬 삭제")
            except OSError:
                say("  /board 스킬 삭제(백업 파일은 남김:", SKILL_DIR, ")")
    except OSError:
        pass
    if str(HOME.resolve()) in ("/", str(Path.home().resolve())):
        say("[실패] 설치 폴더 경로가 이상해 지우지 않습니다:", HOME)
        return 1
    if HOME.exists():
        if not ((SCRIPTS / "board-server.py").exists() or CFG.exists()):
            say("  [주의] Session Orchestrator 설치 폴더로 보이지 않아 남겨 둡니다:", HOME)
        elif keep_data:
            for d in [SCRIPTS, *HOME.glob("scripts.*")]:
                shutil.rmtree(d, ignore_errors=True)
            say("  도구 삭제(board 데이터는 남김)")
        else:
            shutil.rmtree(HOME, ignore_errors=True)
            say("  설치 폴더 삭제")
    say("끝. 앱은 응용 프로그램 폴더에서 휴지통으로 옮기면 됩니다. Claude 앱에 고정해 둔 관제 세션은 직접 정리하세요.")
    return 0


def agent_cli(flag: str) -> int:
    if not (SCRIPTS / "board-agent.py").exists():
        print("아직 설치 전입니다. 앱을 한 번 열어 설치하세요.")
        return 1
    return run_script("board-agent.py", flag).returncode


def main() -> int:
    utf8_stdio()
    argv = [a for a in sys.argv[1:] if not a.startswith("-psn_")]  # 옛 macOS Finder 가 붙이는 인자
    if not argv:  # Finder · Launchpad 에서 열었다
        if quick_open():
            return 0
        return setup(interactive=True)
    if argv[0].startswith("-") or argv[0].endswith(".py"):
        return run_python(argv)
    ap = argparse.ArgumentParser(prog="SessionOrchestrator", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="설치 · 업데이트")
    s.add_argument("--workspace", default="")
    s.add_argument("--no-start", action="store_true")
    s.add_argument("--no-open", action="store_true")
    u = sub.add_parser("uninstall", help="제거")
    u.add_argument("--keep-data", action="store_true")
    sub.add_parser("open", help="상황판 열기")
    sub.add_parser("status", help="상주 · 서버 상태")
    sub.add_parser("version", help="앱 판")
    a = ap.parse_args(argv)
    if a.cmd == "setup":
        return setup(a.workspace, start=not a.no_start, open_=not a.no_open)
    if a.cmd == "uninstall":
        return uninstall(a.keep_data)
    if a.cmd == "open":
        return agent_cli("--open")
    if a.cmd == "status":
        return agent_cli("--status")
    print(app_version())
    return 0


if __name__ == "__main__":
    sys.exit(main())
