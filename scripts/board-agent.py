"""board-agent.py — Session Orchestrator macOS 상주 (Windows 의 board-tray.py 자리).

Windows 트레이와 같은 일을 한다. 메뉴 막대 아이콘은 PyObjC 가 있을 때만 그린다 — macOS 앱(so_mac.py 로 묶은 .app)엔 들어 있고,
install.sh 로 깐 시스템 파이썬엔 보통 없어 아이콘 없이 돈다(표준 라이브러리만으로는 메뉴 막대에 못 그린다).
  1. 상황판 서버(board-server.py)를 품는다. 127.0.0.1:<포트>(board/config.json 의 port, 기본 18799)가 닫혀 있으면 이 프로세스 안에서
     띄우고, 이미 떠 있으면 그걸 쓴다. 그 포트를 못 쓰면 다른 후보로 옮겨 config.json 에 적는다.
     못 띄운 이유는 board/agent-status.json · board/agent.log 에 남고 --status · --open 이 보여 준다.
  2. 알림(osascript display notification): 질문(세션이 묻고 멈춤) · 턴 끝남(20초 넘게 돌던 세션) · 멈춤(도구 호출 뒤 1분 넘게 응답 없음)
     · 세션 간 요청 30분째 미처리 · 갱신 뒤 새로 생긴 내 답 대기. 세는 규칙은 board-tray.py 의 tick·notify 와 같다(고칠 땐 둘 다).
     osascript 알림은 누르면 스크립트 편집기가 열린다. 상황판은 --open 이나 북마크로 연다.
  3. 로그인 시 자동 실행 = launchd LaunchAgent(~/Library/LaunchAgents/local.session-orchestrator.plist).

실행
  python3 -I scripts/board-agent.py                    상주 시작(포그라운드). 이미 떠 있으면 바로 끝난다(잠금 파일 board/agent.lock).
  python3 -I scripts/board-agent.py --install          LaunchAgent 를 쓰고 launchctl 로 띄운다(로그인 때마다 자동 실행, 비정상 종료면 다시 띄움).
            [--python 경로]                             LaunchAgent 가 쓸 파이썬(기본: 지금 이 파이썬). install.sh 가 찾은 경로를 넘긴다
  python3 -I scripts/board-agent.py --uninstall        LaunchAgent 를 내리고 지운다(상주도 꺼짐).
  python3 -I scripts/board-agent.py --open             상황판을 기본 브라우저로 연다. 서버가 응답하지 않으면 이유를 찍고 끝난다.
  python3 -I scripts/board-agent.py --status           상주 · 서버 상태와 마지막 이유를 찍는다.
  python3 -I scripts/board-agent.py --notify on|off    알림 켜기 · 끄기(떠 있는 상주가 다음 주기에 따른다).
  python3 -I scripts/board-agent.py --test-notify      시험 알림을 하나 보낸다.

설정 board/agent-settings.json(알림), 상태 board/agent-status.json, 로그 board/agent.log · agent-server.log · agent-launchd.log.
board/ 는 gitignore. 외부 패키지 없이 표준 라이브러리만 쓴다. macOS 실기 시험 전이다(README 「macOS 시험 절차」).
"""
import argparse
import errno
import importlib.util
import json
import os
import plistlib
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE.parent
ROOT = WS / "board"
CONFIG = ROOT / "config.json"
SETTINGS = ROOT / "agent-settings.json"
STATUS = ROOT / "agent-status.json"
LOCK = ROOT / "agent.lock"
LOG = ROOT / "agent.log"
SERVER_LOG = ROOT / "agent-server.log"   # 서버 접근 로그 · 추적 스레드 오류(stdout · stderr)
LAUNCHD_LOG = ROOT / "agent-launchd.log"  # 이 파일이 stdout 을 돌리기 전에 난 오류(launchd 가 받음)
LABEL = "local.session-orchestrator"
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
TITLE = "Session Orchestrator"
ALT_PORTS = (18799, 28799, 38799, 17799, 27799, 37799)  # board-tray.py 와 같은 후보
POLL = 3.0                # 서버 확인 · 알림 판정 주기(초)
TURN_MIN_WORK = 20        # 이만큼(초) 넘게 돌던 세션이 턴을 끝내면 알림
UNREAD_ALERT = 30 * 60    # 세션 간 요청이 미처리로 이만큼 지나면 알림
TOAST_GAP = 6             # 알림 사이 최소 간격(초). 그새 3개 넘게 쌓이면 하나로 묶는다
IN_USE = {10048, errno.EADDRINUSE}  # 포트 사용 중(Windows WSAEADDRINUSE · POSIX)


def read_json(path: Path, default):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, type(default)) else default
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj):
    tmp = path.with_name(path.name + ".tmp")  # 쓰다 만 파일을 다른 쪽이 읽지 않게 임시 파일에 쓰고 바꿔 끼운다
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def read_config() -> dict:
    return read_json(CONFIG, {})


def valid_port(v, default=18799) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return v if 1024 <= v <= 65535 else default


def load_settings() -> dict:
    return read_json(SETTINGS, {})


def log(*a):
    line = time.strftime("%m-%d %H:%M:%S ") + " ".join(str(x) for x in a) + "\n"
    try:
        if LOG.exists() and LOG.stat().st_size > 512_000:
            LOG.replace(LOG.with_suffix(".log.1"))
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


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


def tally(board: Board, snap):
    """실시간 스냅숏 + 판정 데이터 → (live, eff, running, asking). 상황판 열 규칙 · 트레이 배지와 같은 셈.
    live = 관제를 뺀 세션(짝 없는 대화 파일은 "cli:<id>"), eff = 내 답 대기로 셀 세션
    (판정상 내 답 대기 + 질문을 남기고 멈춘 세션, 정리 후보 제외 − 지금 도는 세션)."""
    snap = snap or {}
    live = {sid: e for sid, e in (snap.get("sessions") or {}).items() if sid != board.control}
    for u in snap.get("unmapped") or []:
        live.setdefault("cli:" + u.get("cli", ""), u)
    running = [k for k, e in live.items() if e.get("state") in ("working", "waiting_tool")]
    asking = {k for k, e in live.items() if e.get("state") == "asking" and not (e.get("ask") or {}).get("weak")}
    eff = set()
    gone = set(snap.get("gone") or [])  # 앱에서 삭제 · 보관된 세션(macOS 는 앱 기록을 아직 안 읽어 늘 비어 있음)
    for r in board.rows:
        sid, st = r.get("_sid"), r.get("status")
        if not sid or st == "관제" or sid in gone:
            continue
        e = live.get(sid) or {}
        ls = e.get("state")
        if ls == "asking" and (e.get("ask") or {}).get("weak"):
            ls = "idle"  # 글로 추정했는데 앱 판정이 없는 질문은 세지 않는다
        if ls in ("working", "waiting_tool"):
            continue
        jt = md_ts(r.get("judged_at"))
        fresh = (e.get("ts") or 0) > jt     # 판정 뒤에 다시 움직임
        replied = (e.get("uts") or 0) > jt  # 판정 뒤에 작업자가 직접 답함
        if ls == "asking" and (st != "정리 후보" or fresh):
            eff.add(sid)
        elif st == "내 답 대기" and not (ls == "idle" and replied):
            eff.add(sid)
    eff |= {k for k in asking if k.startswith("cli:")}
    eff |= {k for k in asking if k.startswith("local_") and k not in board.title}  # 앱 기록으로 짝지어진 판정 전 세션
    return live, eff, running, asking


class Notifier:
    """언제 무엇을 알릴지(board-tray.py notify · pump_toasts 와 같은 규칙). 실제로 띄우는 건 send(title, body)."""

    def __init__(self, send, enabled=lambda: True):
        self.send = send
        self.enabled = enabled
        self.prev = {}
        self.work_since = {}
        self.seen_edges = set()
        self.seen_wait = None
        self.toasts = deque()
        self.last_toast = 0.0
        self.first = True

    def queue(self, title, body):
        if self.enabled():
            self.toasts.append((title, body))

    def update(self, live, edges, waiting, name):
        now = time.time()
        quiet = self.first  # 첫 판정은 기준만 잡고 알리지 않는다
        for k, e in live.items():
            st, prev = e.get("state"), self.prev.get(k)
            if st != "idle":
                self.work_since.setdefault(k, now)
            if not quiet:
                if st == "asking" and prev != "asking" and not (e.get("ask") or {}).get("weak"):
                    q = e.get("ask") or {}
                    ops = " / ".join(q.get("options") or [])
                    self.queue("❓ 질문 · " + name(k), first_line(q.get("text"), 160) + (f"\n선택지: {ops}" if ops else ""))
                elif st == "idle" and prev in ("working", "waiting_tool") and e.get("label") == "턴 끝남" \
                        and now - self.work_since.get(k, now) >= TURN_MIN_WORK:
                    self.queue("✅ 턴 끝남 · " + name(k), first_line(e.get("text")) or "응답을 마쳤어요")
                elif st == "waiting_tool" and prev != "waiting_tool":
                    self.queue("⏸ 멈춘 것 같아요 · " + name(k), e.get("label", ""))
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
                           f"{name(e.get('from'))} → {name(e.get('to'))}: {first_line(e.get('summary'), 80)}")
        cur = {r["_sid"] for r in waiting if r.get("_sid")}
        if self.seen_wait is not None and not quiet:
            for r in waiting:
                if r.get("_sid") and r["_sid"] not in self.seen_wait and (live.get(r["_sid"]) or {}).get("state") != "asking":
                    self.queue("⏳ 내 답 대기 · " + r.get("session", ""), first_line(r.get("todo")))
        self.seen_wait = cur
        self.first = False

    def pump(self):
        if not self.enabled():
            self.toasts.clear()
            return
        if not self.toasts or time.time() - self.last_toast < TOAST_GAP:
            return
        if len(self.toasts) >= 3:
            items = list(self.toasts)
            self.toasts.clear()
            title, body = f"🎼 알림 {len(items)}개", "\n".join(t for t, _ in items)
        else:
            title, body = self.toasts.popleft()
        self.last_toast = time.time()
        self.send(title, body)


# 글은 스크립트에 끼워 넣지 않고 인자(argv)로 넘긴다 — 세션 이름에 따옴표 · 역슬래시가 있어도 AppleScript 가 깨지지 않게
OSA_SCRIPT = ("on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run")


def osascript_cmd(title: str, body: str):
    cmd = ["osascript"]
    for line in OSA_SCRIPT:
        cmd += ["-e", line]
    return cmd + [title[:63], (body or " ")[:250]]


def mac_notify(title: str, body: str) -> bool:
    try:
        r = subprocess.run(osascript_cmd(title, body), capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        log("알림 실패:", e)
        return False
    if r.returncode:
        log("알림 실패:", (r.stderr or b"").decode("utf-8", "replace").strip()[:200])
        return False
    log("알림:", title)
    return True


class Agent:
    def __init__(self):
        self.board = Board()
        self.port = valid_port(read_config().get("port"))
        self.httpd = None
        self.own_server = False
        self.status = "시작 중"
        self.armed = False
        self.ok = False
        self.live_titles = {}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 시스템 프록시 무시(127.0.0.1)
        self.notifier = Notifier(mac_notify, lambda: load_settings().get("notify", True))
        self.last_state = None
        self.view = {"ok": False, "waiting": 0, "asking": 0, "running": 0, "out": 0}  # 메뉴 막대가 읽는 마지막 상태(tick 이 통째로 바꿔 끼움)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ── 서버 ──
    def get(self, path, token=False, timeout=2.0):
        req = urllib.request.Request(self.base + path)
        if token:
            try:
                req.add_header("X-Board-Token", (ROOT / ".board-token").read_text(encoding="utf-8").strip())
            except OSError:
                pass
        with self.opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def set_status(self, s):
        """서버 상태 · 못 띄운 이유. 바뀔 때만 로그에 남긴다(3초마다 같은 줄이 쌓이지 않게)."""
        if s != self.status:
            log("server:", s)
        self.status = s

    def bind(self, mod):
        """포트를 잡는다. config 포트를 못 쓰면 ALT_PORTS 를 차례로 시도하고, 옮긴 포트는 config.json 에 적는다.
        config 포트가 '사용 중'인데 우리 서버가 거기서 답하면(관제가 직접 띄운 서버 등) None — 그 서버를 쓴다."""
        tried = []
        for p in dict.fromkeys((self.port,) + ALT_PORTS):
            try:
                httpd = mod.Server(("127.0.0.1", p), mod.Handler)
            except OSError as e:
                code = getattr(e, "winerror", None) or e.errno
                tried.append(f"{p}(오류 {code})")
                if p == self.port and code in IN_USE:
                    try:
                        self.get("/api/health", timeout=3)
                        return None
                    except Exception:
                        pass
                continue
            if p != self.port:
                log("port:", self.port, "못 씀 →", p, "로 옮김 · 시도:", ", ".join(tried))
                self.port = p
                cfg = read_config()
                cfg["port"] = p
                try:
                    write_json(CONFIG, cfg)
                except OSError as e:
                    log("config 쓰기:", e)
            return httpd
        raise OSError("쓸 수 있는 포트가 없음 — 시도: " + ", ".join(tried))

    def start_server(self) -> bool:
        if not (ROOT / "session-board.html").exists():
            self.set_status("상황판 파일 없음 — install.sh 를 다시 실행하세요")
            return False
        httpd = None
        try:
            log("server: 띄우는 중 — 서버 모듈 읽기")
            spec = importlib.util.spec_from_file_location("board_server", HERE / "board-server.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for d in mod.STATES:
                (ROOT / d).mkdir(exist_ok=True)
            mod.Handler.root = ROOT
            log("server: 포트 잡기", self.port)
            httpd = self.bind(mod)  # 포트를 먼저 잡는다. 못 잡으면 대화 파일 감시 스레드를 띄우지 않는다
            if httpd is None:
                self.set_status("다른 곳에서 띄운 서버를 씀")
                return False
            target = mod.board_live.watched_workspace(ROOT)  # 감시할 작업 폴더(board/config.json, 없으면 이 도구의 부모 폴더)
            mod.Handler.app = mod.AppSessions(target)  # macOS 에선 앱 세션 기록 위치를 아직 확인 못 해 빈 결과(그 기능만 꺼짐)
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
        self.set_status("이 상주가 띄움")
        log("server: 이 상주 안에서 띄움", self.base)
        return True

    def ensure_server(self) -> bool:
        try:
            self.armed = bool(self.get("/api/health", timeout=1.5).get("armed"))
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

    # ── 주기 작업 ──
    def name(self, sid):
        if sid in self.board.title:
            return self.board.title[sid]
        t = self.live_titles.get(sid)  # 판정 전 새 세션: 대화 파일의 사이드바 제목
        return f"{t} (판정 전)" if t else "새 세션(판정 전)"

    def tick(self):
        self.ok = self.ensure_server()
        self.board.reload()
        snap = None
        if self.ok:
            try:
                snap = self.get("/api/live", token=True, timeout=2.5)
            except Exception as e:
                log("live:", e)
        live, eff, running, asking = tally(self.board, snap)
        self.live_titles = {k: e.get("title") for k, e in live.items() if e.get("title")}
        out = (((snap or {}).get("totals") or {}).get("today") or {}).get("output_tokens", 0)
        self.view = {"ok": self.ok, "waiting": len(eff), "asking": len(asking & eff), "running": len(running), "out": out}
        self.write_status(eff, running, asking, out)
        if snap and snap.get("ready"):
            self.notifier.update(live, snap.get("edges") or [], self.board.waiting(), self.name)
        self.notifier.pump()

    def write_status(self, eff, running, asking, out):
        """트레이 툴팁 · 메뉴 대신. --status · --open · install.sh 가 읽는다. 바뀔 때만 쓴다."""
        if self.own_server:
            server = "이 상주가 띄움"
        elif self.ok:
            server = "다른 곳에서 띄운 서버를 씀"
        else:
            server = "꺼짐 — " + self.status
        state = {"pid": os.getpid(), "port": self.port, "server": server, "control_waiting": self.armed,
                 "waiting": len(eff), "asking": len(asking & eff), "running": len(running), "today_output_k": round(out / 1000)}
        if state == self.last_state:
            return
        self.last_state = state
        try:
            write_json(STATUS, dict(state, at=time.strftime("%Y-%m-%d %H:%M:%S")))
        except OSError as e:
            log("status 쓰기:", e)

    def run(self):
        log("시작", sys.executable, "pid", os.getpid())
        while True:
            try:
                self.tick()
            except Exception as e:  # 한 주기 실패로 상주가 죽지 않게
                log("tick:", repr(e))
            time.sleep(POLL)


# ── 단일 실행 · 출력 ──
def take_lock():
    """board/agent.lock 을 배타 잠금. 이미 다른 상주가 잡고 있으면 None. 잠금은 파일을 닫거나 프로세스가 끝나면 풀린다."""
    import fcntl  # macOS · Linux 전용이라 여기서만 부른다(Windows 에서도 이 파일을 불러 시험할 수 있게)
    ROOT.mkdir(exist_ok=True)
    f = open(LOCK, "a+", encoding="utf-8")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def lock_held() -> bool:
    """다른 상주가 떠 있는지(잠금을 잠깐 잡아 본다)."""
    f = take_lock()
    if f is None:
        return True
    f.close()
    return False


def agent_state() -> str:
    try:
        return "떠 있음" if lock_held() else "안 떠 있음"
    except Exception as e:  # fcntl 이 없는 OS 등
        return f"확인 못 함({e})"


RESTART_HINT = f"launchctl kickstart -k gui/$(id -u)/{LABEL}   (안 되면 Session Orchestrator 앱을 다시 열거나 install.sh 를 다시 실행)"


# ── 메뉴 막대 아이콘(macOS, PyObjC 가 있을 때만 — 앱 번들엔 들어 있고, install.sh 로 깐 시스템 파이썬엔 보통 없어 아이콘 없이 돈다) ──
MB_BARS = (12, 19, 10, 16)  # board-tray.py 로고 A 와 같은 막대 높이(32 격자). 고칠 땐 둘 다
MB_FRAMES = ((1, 1, 1, 1), (.6, 1.12, .75, 1.25), (1.25, .65, 1.3, .7), (.85, .9, 1.1, .85))  # 도는 동안 번갈아 그리는 높이 배율


def _in_rr(x, y, x0, y0, x1, y1, r):
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _png(size: int, rgba: bytes) -> bytes:
    import struct
    import zlib
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def bars_png(px=36, frame=0, offline=False) -> bytes:
    """메뉴 막대용 템플릿 그림(검정 + 투명 — macOS 가 밝은 · 어두운 막대에 맞춰 칠한다). 막대 4개, 서버가 꺼지면 내려앉는다."""
    k = px / 32
    w = max(2, round(4 * k))
    gap = max(1, round(2 * k))
    x0 = round((px - (4 * w + 3 * gap)) / 2)
    base = round(27 * k)
    bars = []
    for i, h in enumerate(MB_BARS):
        hh = 5 if offline else min(22, h * MB_FRAMES[frame % len(MB_FRAMES)][i])
        x = x0 + i * (w + gap)
        bars.append((x, base - max(w, round(hh * k)), x + w, base, w / 2))
    ss = 3
    out = bytearray(px * px * 4)
    for py in range(px):
        for qx in range(px):
            n = 0
            for sy in range(ss):
                y = py + (sy + .5) / ss
                for sx in range(ss):
                    x = qx + (sx + .5) / ss
                    if any(_in_rr(x, y, *b) for b in bars):
                        n += 1
            if n:
                out[(py * px + qx) * 4 + 3] = n * 255 // (ss * ss)  # RGB 는 0(검정), 알파만
    return _png(px, bytes(out))


def has_appkit() -> bool:
    if os.environ.get("SO_NO_MENUBAR") or sys.platform != "darwin":
        return False
    try:
        return importlib.util.find_spec("AppKit") is not None
    except (ImportError, ValueError):
        return False


def run_menubar(agent, cleanup):
    """메뉴 막대 아이콘. 주 스레드에서 NSApplication 을 돌리고, 주기 작업(agent.run)은 배경 스레드가 맡는다.
    0.38초마다 타이머가 agent.view 를 읽어 아이콘(도는 세션이 있으면 막대가 오르내림) · 내 답 대기 숫자 · 메뉴 글을 고친다
    — 다른 스레드에서 화면을 건드리지 않는다. 아이콘을 누르면 메뉴(상황판 열기 · 알림 · 자동 실행 · 상태 · 종료)."""
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSImage, NSImageLeft, NSMenu,
                        NSMenuItem, NSStatusBar, NSVariableStatusItemLength)
    from Foundation import NSData, NSObject, NSTimer

    images = {}
    st = {"frame": 0, "key": None, "n": 0}

    def image(frame, offline):
        key = (frame, offline)
        if key not in images:
            png = bars_png(36, frame, offline)
            img = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(png, len(png)))
            img.setSize_((18, 18))
            img.setTemplate_(True)
            images[key] = img
        return images[key]

    def open_now():
        if agent.ok:
            subprocess.run(["open", agent.base + "/"])
        else:
            mac_notify("상황판 서버를 못 띄웠어요", f"{agent.status}\n기록: {LOG}")

    def paint():
        v = agent.view
        ok, running, waiting = v.get("ok"), v.get("running", 0), v.get("waiting", 0)
        st["frame"] = (st["frame"] + 1) % len(MB_FRAMES) if (ok and running) else 0
        key = (st["frame"], not ok, waiting if ok else 0)
        if key != st["key"]:
            st["key"] = key
            btn.setImage_(image(st["frame"], not ok))
            btn.setTitle_(f" {waiting}" if ok and waiting else "")
        st["n"] += 1
        if st["n"] % 8 != 1:  # 글 · 체크 표시는 3초에 한 번(설정 파일을 0.38초마다 읽지 않게)
            return
        if ok:
            tip = f"{TITLE}\n내 답 대기 {waiting}(질문 {v.get('asking', 0)}) · 도는 세션 {running} · 오늘 출력 {v.get('out', 0) / 1000:.0f}k"
            server = f"서버: {'이 상주가 띄움' if agent.own_server else '다른 곳에서 띄움'} (포트 {agent.port})"
        else:
            tip = f"{TITLE}\n서버에 연결 못 함 — {agent.status}"
            server = "서버: 꺼짐 — " + agent.status[:80]
        btn.setToolTip_(tip)
        m_counts.setTitle_(tip.split("\n", 1)[1])
        m_server.setTitle_(server)
        m_armed.setTitle_("관제 세션 대기: " + ("연결됨" if agent.armed else "아님 — 채팅 릴레이 멈춤"))
        m_notify.setState_(1 if load_settings().get("notify", True) else 0)
        m_auto.setState_(1 if PLIST.exists() else 0)

    class SOMenuBarController(NSObject):
        def tick_(self, _timer):
            try:
                paint()
            except Exception as e:
                log("menubar:", repr(e))

        def openBoard_(self, _sender):
            threading.Thread(target=open_now, daemon=True).start()

        def toggleNotify_(self, _sender):
            s = load_settings()
            s["notify"] = not s.get("notify", True)
            try:
                write_json(SETTINGS, s)
            except OSError as e:
                log("settings:", e)
            st["n"] = 0

        def testNotify_(self, _sender):
            threading.Thread(target=mac_notify, daemon=True, args=(
                "🎼 알림 시험", "이렇게 떠요. 안 보이면 시스템 설정 → 알림에서 '스크립트 편집기' 알림을 켜 주세요.")).start()

        def toggleAutostart_(self, _sender):
            try:
                if PLIST.exists():
                    PLIST.unlink()  # 지금 도는 상주는 그대로 두고 다음 로그인부터 안 띄운다(bootout 하면 이 프로세스가 꺼짐)
                    log("자동 실행 끔:", PLIST)
                else:
                    PLIST.parent.mkdir(parents=True, exist_ok=True)
                    PLIST.write_bytes(plistlib.dumps(plist_dict(sys.executable)))
                    log("자동 실행 켬:", PLIST)
            except OSError as e:
                log("autostart:", e)
            st["n"] = 0

        def quit_(self, _sender):
            cleanup()
            os._exit(0)  # 0 이라 launchd 가 다시 띄우지 않는다(다음 로그인 때 다시 뜸)

    ctl = SOMenuBarController.alloc().init()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # 독 · ⌘Tab 에 안 나오고 메뉴 막대에만
    item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
    btn = item.button()
    btn.setImage_(image(0, True))
    btn.setImagePosition_(NSImageLeft)
    menu = NSMenu.alloc().init()
    menu.setAutoenablesItems_(False)

    def add(title, action=None):
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        if action:
            mi.setTarget_(ctl)
        else:
            mi.setEnabled_(False)
        menu.addItem_(mi)
        return mi

    add("상황판 열기", "openBoard:")
    menu.addItem_(NSMenuItem.separatorItem())
    m_notify = add("알림 받기", "toggleNotify:")
    add("알림 시험 보내기", "testNotify:")
    m_auto = add("로그인 시 자동 실행", "toggleAutostart:")
    menu.addItem_(NSMenuItem.separatorItem())
    m_counts = add("시작 중")
    m_server = add("서버: 시작 중")
    m_armed = add("관제 세션 대기: 확인 중")
    menu.addItem_(NSMenuItem.separatorItem())
    add("종료", "quit:")
    item.setMenu_(menu)
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.38, ctl, "tick:", None, True)
    keep = (ctl, item, menu, timer)  # noqa: F841 — 참조를 붙들어 둔다(GC 되면 아이콘이 사라짐)
    log("메뉴 막대 아이콘 시작")
    app.run()


def redirect_output():
    """launchd 아래에선 stdout · stderr 가 agent-launchd.log 로 가는데 크기 제한이 없다.
    서버 접근 로그 · 추적 스레드 오류는 시작할 때 크기를 보고 갈아 주는 agent-server.log 로 돌린다. 터미널에서 띄우면 그대로 둔다."""
    if sys.stderr is not None and sys.stderr.isatty():
        return
    try:
        if SERVER_LOG.exists() and SERVER_LOG.stat().st_size > 1_000_000:
            SERVER_LOG.replace(SERVER_LOG.with_suffix(".log.1"))
        f = open(SERVER_LOG, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = sys.stderr = f


# ── launchd ──
def plist_dict(py: str) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [py, "-I", str(HERE / "board-agent.py")],
        "WorkingDirectory": str(WS),
        "RunAtLoad": True,                         # 로그인할 때(그리고 등록하는 지금) 띄운다
        "KeepAlive": {"SuccessfulExit": False},    # 비정상 종료면 다시 띄운다. 이미 떠 있어서 스스로 끝낸 것(0)은 다시 안 띄움
        "StandardOutPath": str(LAUNCHD_LOG),
        "StandardErrorPath": str(LAUNCHD_LOG),
    }


def launchctl(*args):
    try:
        r = subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)
    return r.returncode, " ".join((r.stderr or r.stdout or "").split())[:300]


def install(py: str) -> int:
    """LaunchAgent 를 쓰고 등록한다. 등록이 안 되면 이번만 직접 띄운다(로그인 자동 실행은 안 됨)."""
    ROOT.mkdir(exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(plist_dict(py)))
    print("LaunchAgent:", PLIST)
    domain = f"gui/{os.getuid()}"
    launchctl("bootout", f"{domain}/{LABEL}")  # 옛 등록을 내린다(없으면 오류, 무시)
    msg = ""
    for _ in range(5):  # 막 내린 직후엔 'Bootstrap failed: 5' 가 날 수 있어 1초씩 다시
        code, msg = launchctl("bootstrap", domain, str(PLIST))
        if code == 0:
            print("상주를 띄웠습니다(로그인할 때마다 자동 실행).")
            return 0
        time.sleep(1)
    code, msg2 = launchctl("load", "-w", str(PLIST))  # 옛 macOS 방식
    if code == 0 and not msg2:
        print("상주를 띄웠습니다(launchctl load, 로그인할 때마다 자동 실행).")
        return 0
    print("[주의] launchctl 등록 실패:", msg or msg2, "— 이번만 직접 띄웁니다(로그인 자동 실행은 안 됨)")
    subprocess.Popen([py, "-I", str(HERE / "board-agent.py")], cwd=str(WS), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def uninstall() -> int:
    launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    if PLIST.exists():
        launchctl("unload", str(PLIST))  # bootout 이 안 먹는 옛 macOS
        PLIST.unlink()
        print("LaunchAgent 삭제:", PLIST)
    else:
        print("LaunchAgent 없음:", PLIST)
    return 0


# ── 명령 ──
def health(port: int, timeout=2.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def open_board() -> int:
    port = valid_port(read_config().get("port"))
    url = f"http://127.0.0.1:{port}/"
    try:
        health(port)
    except Exception as e:
        print(f"상황판 서버가 응답하지 않습니다({url}): {e}")
        print("  상주:", agent_state(), "· 마지막 기록:", read_json(STATUS, {}).get("server") or "없음")
        print("  다시 띄우기:", RESTART_HINT)
        print("  로그:", LOG)
        return 1
    if sys.platform == "darwin":
        subprocess.run(["open", url])
    else:
        webbrowser.open(url)
    print("열었습니다:", url)
    return 0


def show_status() -> int:
    port = valid_port(read_config().get("port"))
    state = agent_state()
    print("상주:", state + ("" if state == "떠 있음" else f" — 다시 띄우기: {RESTART_HINT}"))
    print("LaunchAgent:", PLIST if PLIST.exists() else "없음(로그인 자동 실행 꺼짐)")
    try:
        h = health(port)
        print(f"서버: http://127.0.0.1:{port}/ 응답함 · 관제 대기 {'연결됨' if h.get('armed') else '아님(채팅 릴레이 멈춤)'} · 쌓인 요청 {h.get('queued', 0)}")
    except Exception as e:
        print(f"서버: http://127.0.0.1:{port}/ 응답 없음 — {e}")
    st = read_json(STATUS, {})
    if st:
        print(f"마지막 상태({st.get('at', '')}): 서버 {st.get('server')} · 내 답 대기 {st.get('waiting')}(질문 {st.get('asking')})"
              f" · 도는 세션 {st.get('running')} · 오늘 출력 {st.get('today_output_k')}k")
    print("알림:", "켜짐" if load_settings().get("notify", True) else "꺼짐")
    print("로그:", LOG)
    return 0


def set_notify(on: bool) -> int:
    s = load_settings()
    s["notify"] = on
    ROOT.mkdir(exist_ok=True)
    write_json(SETTINGS, s)
    print("알림:", "켜짐" if on else "꺼짐")
    return 0


def run_agent() -> int:
    ROOT.mkdir(exist_ok=True)
    lock = None
    for _ in range(10):  # --install 이 옛 상주를 내리자마자 새로 띄우면 옛 것이 잠금을 놓을 때까지 잠깐 기다린다
        lock = take_lock()
        if lock is not None:
            break
        time.sleep(0.5)
    if lock is None:
        log("이미 다른 상주가 떠 있어 끝냄")
        return 0
    redirect_output()
    try:  # 멈춘 것 같으면 kill -USR1 <pid> → 모든 스레드의 지금 위치를 agent-server.log(터미널이면 화면)에 찍는다
        import faulthandler
        faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    except (ImportError, AttributeError, ValueError, OSError):
        pass
    agent = Agent()
    done = []

    def cleanup():
        if done:
            return
        done.append(1)
        log("종료")
        st = read_json(STATUS, {})
        if st.get("pid") == os.getpid():  # 꺼진 뒤에 낡은 「이 상주가 띄움」이 남지 않게
            try:
                write_json(STATUS, dict(st, server="꺼짐 — 상주가 끝남", at=time.strftime("%Y-%m-%d %H:%M:%S")))
            except OSError:
                pass
        lock.close()

    if has_appkit():
        # 메뉴 막대: 주 스레드는 NSApplication 이 차지하므로 주기 작업은 배경 스레드. 끝낼 땐 정리 후 바로 나간다(0 = 다시 안 띄움)
        def on_term(*_):
            cleanup()
            os._exit(0)
        signal.signal(signal.SIGTERM, on_term)
        threading.Thread(target=agent.run, daemon=True, name="agent").start()
        try:
            run_menubar(agent, cleanup)
        except Exception as e:  # 아이콘을 못 그려도 서버 · 알림은 계속 돈다
            log("메뉴 막대 실패 — 아이콘 없이 계속:", repr(e))
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        cleanup()
        return 0

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # launchctl bootout · 로그아웃 → 정상 종료(0 이라 다시 안 띄움)
    try:
        agent.run()
    except KeyboardInterrupt:  # 터미널에서 띄웠다가 Ctrl+C
        pass
    except Exception as e:
        log("치명:", repr(e))
        raise
    finally:
        cleanup()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="LaunchAgent 등록 + 띄우기")
    g.add_argument("--uninstall", action="store_true", help="LaunchAgent 내리기 + 지우기")
    g.add_argument("--open", action="store_true", help="상황판을 기본 브라우저로 열기")
    g.add_argument("--status", action="store_true", help="상주 · 서버 상태")
    g.add_argument("--notify", choices=("on", "off"), help="알림 켜기 · 끄기")
    g.add_argument("--test-notify", action="store_true", help="시험 알림 보내기")
    ap.add_argument("--python", default=sys.executable, help="--install 때 LaunchAgent 가 쓸 파이썬 경로")
    a = ap.parse_args()
    if a.install:
        return install(a.python)
    if a.uninstall:
        return uninstall()
    if a.open:
        return open_board()
    if a.status:
        return show_status()
    if a.notify:
        return set_notify(a.notify == "on")
    if a.test_notify:
        ok = mac_notify("🎼 알림 시험", "이렇게 떠요. 안 보이면 시스템 설정 → 알림에서 '스크립트 편집기' 알림을 켜 주세요.")
        print("보냈습니다." if ok else f"보내지 못했습니다(로그: {LOG}).")
        return 0 if ok else 1
    return run_agent()


if __name__ == "__main__":
    sys.exit(main())
