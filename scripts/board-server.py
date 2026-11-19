"""board-server.py — 상황판 로컬 서버 (127.0.0.1 전용).

역할 두 가지:
1. board/ 폴더를 정적으로 서빙한다 (http://127.0.0.1:18799/ → session-board.html).
2. 상황판 페이지가 보내는 세션 메시지를 board/inbox/<id>.json 으로 떨군다.
   실제 처리는 관제 세션이 한다: `scripts/board-queue.py wait` 로 inbox 를 지켜보다가 깨어나
   관제 세션 자신에게 온 메시지(예: "갱신")면 그대로 수행하고, 다른 세션 앞이면 send_message 로 전달한 뒤 done/ 으로 옮긴다.
   서버는 Claude 를 직접 부르지 못한다. 관제 세션이 대기 중이 아니면 요청은 inbox 에 남는다.

사용: python -I scripts/board-server.py [--root board] [--port 18799]
- /board 스킬이 포트가 닫혀 있을 때만 백그라운드로 띄운다(중복 기동 금지).
- 인증: board/.board-token (렌더러가 만들고 페이지에 박아 둔 값) 을 X-Board-Token 헤더로 받는다.
  다른 로컬 페이지가 멋대로 세션에 글을 넣지 못하게 하는 최소 장치다. 토큰은 리포에 올라가지 않는다(board/ 는 gitignore).

API
  GET  /api/health              → {ok, armed(관제 세션 대기 중인지), queued(inbox 개수)}
  GET  /api/status?id=…         → {state: queued|working|done|error|unknown, result?}
  POST /api/message             → {id, state:'queued'}            (본문 {session_id, session, text})
  POST /api/archive             → {id, state:'queued'}            (본문 {session_ids:[…], sessions:[제목…]})  정리 후보 보관
  POST /api/summarize           → {id, state:'queued'}            (본문 {session_id, session})  카드 상세의 「요약 만들기」
  POST /api/delete              → {id, state:'queued'}            (본문 {session_ids:[…≤25], sessions:[…]})  정리 후보 삭제(앱 승인 카드를 거침)
  POST /api/map                 → {id, state:'queued'}            (본문 {cli, title})  판정 전 새 세션 카드의 「세션 찾기」 — 관제가 사이드바 세션과 짝지어 행을 붙인다
  GET  /api/live?t=토큰         → 실시간 스냅숏(세션별 상태·도구·마지막 문장·토큰, 오늘 토큰 합계) — board_live.Tracker
  GET  /api/stream?t=토큰       → 같은 스냅숏을 SSE 로 밀어 줌(바뀔 때마다, 최소 2초 간격). EventSource 는 헤더를 못 붙여 토큰을 쿼리로 받는다
  GET  /api/timeline?hours=N    → 세션 타임라인(활동 구간·메시지 이벤트), N=1~168
  GET  /api/heatmap             → 최근 7일 요일×시간 출력 토큰·활동 분
  GET  /api/doc?path=…          → md 문서 하나(감시 폴더 · 메모리 폴더 안 .md 만, 읽기 전용) — board_docs
  GET  /api/doc/list?sid=…      → 그 세션이 쓰고 · 고치고 · 읽고 · 말한 md 파일(상세 창 「문서」)
  GET  /api/doc/recent          → 최근에 바뀐 md 파일(도구줄 「📄 문서」)

상황판이 직접 돌리는 세션(board_host, Windows): 입력창 글이 그 CLI 프로세스에 바로 들어가 작업자 본인 입력이 된다(관제를 안 거침).
  GET  /pair?c=코드[&s=세션]    → 트레이가 연 창에 입력 열쇠 쿠키를 준다(코드는 트레이 메모리에서 만든 한 번짜리, 60초)
  GET  /api/host/paired         → {paired(이 창이 입력을 보낼 수 있나), cli(CLI 설치), os}
  GET  /api/host/state?sid=…    → 그 세션 프로세스 상태 · 허용/질문 대기 목록 · 앱이 잡고 있는지(app_holds)
  GET  /api/host/list           → 지금 돌리는 세션들
  POST /api/host/send · answer · stop → 보내기(새 세션 포함) · 허용/거부/선택지 답 · 멈추기. 토큰 + 입력 열쇠 쿠키 + 같은 출처가 있어야 한다
  (토큰은 파일에 적혀 있어 같은 PC 의 다른 세션도 읽을 수 있다. 열쇠는 서버 메모리에만 있어, 세션이 curl 로 자기 승인을 보내지 못한다)

정적 서빙은 session-board.html·board-snippet.css 만 내준다(11-10). 예전엔 board/ 전체라 .board-token·판정 데이터가 URL 로 열렸다.

앱 세션 기록(11-10): Claude 데스크톱 앱은 세션마다 %LOCALAPPDATA%/Claude-Data/<패키지>/claude-code-sessions/<계정>/<조직>/local_<id>.json 을 둔다.
사이드바 id(sessionId) ↔ 대화 파일 id(cliSessionId·lastAssistantCliSessionId), 제목, 보관 여부가 들어 있어
판정 전 새 세션도 관제 없이 짝짓고(드로어·보관·삭제·채팅이 되는 카드), 보관한 세션은 자동으로 뺀다. 읽기만 한다.
앱 내부 형식이라 바뀌면 이 부분만 조용히 비고(빈 결과) 판정 데이터·「세션 찾기」 방식으로 돌아간다.
macOS 는 앱 세션 기록 위치를 아직 확인하지 않아 같은 빈 결과로 둔다(LOCALAPPDATA 가 없으면 끔).
"""
import argparse
import glob
import json
import os
import re
import secrets
import socket
import socketserver
import sys
import threading
import time

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I 로 실행해도 같은 폴더의 board_live 를 쓰게
import board_live  # noqa: E402
import board_docs  # noqa: E402
import board_host  # noqa: E402

SID = re.compile(r"^local_[0-9a-f-]{36}$")

# ── 입력 열쇠: 상황판이 직접 돌리는 세션에 보내는 입력(글 · 허용 · 답 · 멈추기) 전용 ──
# 이 입력은 그 세션에 「작업자 본인 입력」으로 들어간다. 그래서 파일(.board-token · 페이지)에 적힌 상황판 토큰만으로는 못 보내게 한다
# (같은 PC 의 다른 세션이 토큰 파일을 읽어 curl 로 자기 승인을 보내는 길). 열쇠는 이 서버 프로세스 메모리에만 있고 파일에 안 쓴다.
# 트레이가 자기가 여는 창에만 한 번 쓰는 연결 코드를 넘기고(/pair), 그 창이 쿠키(HttpOnly · SameSite=Strict)로 받는다.
INPUT_KEY = secrets.token_urlsafe(32)
INPUT_COOKIE = "so_in"
_PAIR = {}  # 연결 코드 → 만료 시각
_PAIR_LOCK = threading.Lock()


def pair_code(ttl=60) -> str:
    """한 번 쓰는 연결 코드(ttl 초 안에 /pair?c= 로 한 번만 통함). 트레이 · 직접 실행 main 만 부른다."""
    c = secrets.token_urlsafe(24)
    now = time.time()
    with _PAIR_LOCK:
        for k in [k for k, t in _PAIR.items() if t < now]:
            _PAIR.pop(k, None)
        _PAIR[c] = now + ttl
    return c


def _take_pair(c) -> bool:
    with _PAIR_LOCK:
        t = _PAIR.pop(str(c or ""), None)
    return bool(t) and t >= time.time()
RID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
CLI = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_TEXT = 4000
STATES = ("done", "working", "inbox")
MARKABLE = ("내 답 대기", "완료", "정리 후보")  # 카드 상태 드롭다운으로 작업자가 직접 옮길 수 있는 열(실행 중은 실시간 상태라 제외)
_MARK_LOCK = threading.Lock()
STATIC = {"/session-board.html", "/board-snippet.css"}  # 이 둘만 정적으로 내준다


class AppSessions:
    """앱 세션 기록 읽기. 가장 최근에 갱신된 계정·조직 폴더 하나만(계정 이전 전 옛 폴더 제외), 작업 폴더(와 그 워크트리) 세션만."""

    def __init__(self, workspace: Path):
        self.ws = str(workspace).lower().rstrip("\\/")
        self.dir = None
        self.dir_at = 0.0
        self.cache = {}  # 파일 → (mtime, 요약)

    def _pick_dir(self):
        local = os.environ.get("LOCALAPPDATA")
        if not local:  # Windows 밖(macOS 등): 앱 세션 기록 위치를 아직 확인하지 않아 끈다 → 빈 결과, 나머지는 그대로 돈다
            return None
        best, best_mt = None, 0.0
        base = os.path.join(local, "Claude-Data")
        for d in glob.glob(os.path.join(base, "*", "claude-code-sessions", "*", "*")):
            for f in glob.glob(os.path.join(d, "local_*.json")):
                try:
                    mt = os.path.getmtime(f)
                except OSError:
                    continue
                if mt > best_mt:
                    best, best_mt = d, mt
        return best

    def records(self):
        """{사이드바 id: {cli: [대화 파일 id…], title, archived}}"""
        now = time.time()
        if self.dir is None or now - self.dir_at > 60:
            try:
                self.dir = self._pick_dir()
            except Exception:
                self.dir = None
            self.dir_at = now
        out = {}
        if not self.dir:
            return out
        for f in glob.glob(os.path.join(self.dir, "local_*.json")):
            try:
                mt = os.path.getmtime(f)
                hit = self.cache.get(f)
                if not hit or hit[0] != mt:
                    d = json.loads(Path(f).read_text(encoding="utf-8"))
                    pts = d.get("postTurnSummary") or {}
                    fresh = bool(d.get("postTurnSummaryFor")) and d.get("postTurnSummaryFor") == d.get("lastAssistantUuid")
                    rec = {"sid": d.get("sessionId", ""), "title": str(d.get("title") or "")[:120], "archived": bool(d.get("isArchived")),
                           # 앱이 마지막 턴마다 매기는 상태(completed·blocked)와 작업자 할 일. 마지막 턴 것일 때만 쓴다
                           "app_state": pts.get("status_category") if fresh else None, "needs": str(pts.get("needs_action") or "")[:200],
                           # 앱이 판정한 턴(assistant 줄 uuid). 상황판이 직접 돌린 턴은 앱 기록을 안 고쳐서, 대화 파일의 마지막 턴과 다르면 낡은 판정이다(11-18)
                           "pts_for": str(d.get("postTurnSummaryFor") or ""),
                           # 앱이 턴마다 적는 한 줄 상태 요약(사이드바에 보이는 것). 판정 뒤에 움직인 카드의 「마지막 결과」로 쓴다(11-11)
                           "detail": str(pts.get("status_detail") or "")[:200] if fresh else "",
                           "cwd": str(d.get("cwd") or "").lower(),
                           # 상황판이 이 세션을 CLI 로 이어 돌릴 때 앱과 같은 모델 · 노력 · 권한 모드로(board_host)
                           "model": str(d.get("model") or ""), "effort": str(d.get("effort") or ""),
                           "mode": str(d.get("permissionMode") or ""),
                           "cli": [c for c in {d.get("cliSessionId"), d.get("lastAssistantCliSessionId")} if c]}
                    self.cache[f] = hit = (mt, rec)
                rec = hit[1]
            except Exception:
                continue
            if SID.match(rec["sid"]) and rec["cwd"].startswith(self.ws):
                out[rec["sid"]] = rec
        return out


class Handler(SimpleHTTPRequestHandler):
    root: Path = Path("board")
    tracker = None
    app = None  # AppSessions
    _map = (None, {}, {}, frozenset())  # (board-sessions.json mtime, {cli id: 사이드바 세션 id}, {세션 name: 세션 id}, {판정 행의 세션 id})
    _arch = (None, frozenset())  # (board-archived.json mtime, {보관·삭제한 세션 cli id})

    @classmethod
    def archived(cls):
        """관제가 보관·삭제할 때 적어 두는 cli id 목록. 실시간 층이 이 세션을 임시 카드로 되살리지 않게 한다."""
        p = cls.root / "board-archived.json"
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = None
        if cls._arch[0] != mt:
            try:
                cls._arch = (mt, frozenset(str(x) for x in json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                cls._arch = (mt, frozenset())
        # 앱 기록에서 보관된 세션의 대화 파일도 뺀다
        app_arch = {c for rec in cls.app_records().values() if rec["archived"] for c in rec["cli"]}
        return cls._arch[1] | app_arch

    @classmethod
    def app_records(cls):
        try:
            return cls.app.records() if cls.app else {}
        except Exception:
            return {}

    @classmethod
    def mapping(cls):
        """cli id → 사이드바 id. 판정 데이터의 짝(board-map.py 가 시각으로 추정)에 앱 세션 기록의 짝(정확)을 덮어쓴다."""
        m = dict(cls._board_map())
        for sid, rec in cls.app_records().items():
            if not rec["archived"]:
                for c in rec["cli"]:
                    m[c] = sid
        return m

    @classmethod
    def _board_map(cls):
        p = cls.root / "board-sessions.json"
        try:
            mt = p.stat().st_mtime
        except OSError:
            return {}
        if cls._map[0] != mt:
            m, n, sids = {}, {}, set()
            try:
                for r in json.loads(p.read_text(encoding="utf-8")):
                    cid, link = r.get("cli_id"), r.get("link", "")
                    if "local_" not in link:
                        continue
                    sid = link.rsplit("/", 1)[-1]
                    sids.add(sid)
                    for c in (cid if isinstance(cid, list) else [cid]) if cid else []:
                        m[c] = sid
                    if r.get("name"):
                        n[r["name"]] = sid
            except Exception:
                pass
            cls._map = (mt, m, n, frozenset(sids))
        return cls._map[1]

    @classmethod
    def names(cls):
        cls._board_map()
        return cls._map[2]

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(self.root), **k)

    # 상태 폴링은 로그에서 뺀다(2초마다 찍혀서 시끄럽다)
    def log_message(self, fmt, *args):
        if args and any(x in str(args[0]) for x in ("/api/status", "/api/live", "/api/stream", "/api/health")):
            return
        if args and "/pair?" in str(args[0]):  # 연결 코드는 기록에 남기지 않는다(로그 파일은 다른 세션도 읽을 수 있다)
            args = (re.sub(r"([?&]c=)[^&\s]+", r"\1***", str(args[0])),) + args[1:]
        super().log_message(fmt, *args)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Board-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self, given=None) -> bool:
        p = self.root / ".board-token"
        tok = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        given = self.headers.get("X-Board-Token", "") if given is None else given
        return bool(tok) and secrets.compare_digest(given, tok)

    def _cookie(self, name) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _same_origin(self) -> bool:
        """브라우저 요청이면 이 서버 페이지에서 온 것만(Origin · Sec-Fetch-Site). 다른 사이트 페이지가 보낸 요청을 막는다."""
        o = self.headers.get("Origin")
        if o and urlparse(o).netloc != self.headers.get("Host", ""):
            return False
        sfs = self.headers.get("Sec-Fetch-Site")
        return not sfs or sfs in ("same-origin", "none")

    def _input_ok(self) -> bool:
        """직접 돌리는 세션에 입력을 보내도 되나: 트레이가 연결해 준 창의 쿠키 + 같은 출처."""
        return self._same_origin() and secrets.compare_digest(self._cookie(INPUT_COOKIE), INPUT_KEY)

    def _live(self):
        if not self.tracker:
            return {"ready": False}
        snap = self.tracker.snapshot(self.mapping(), self.names(), self.archived())
        # 판정 행에 없지만 앱 기록으로 짝지어진 세션(판정 전 새 세션) → 페이지가 링크 있는 임시 카드로 그린다
        self._board_map()
        recs = self.app_records()
        snap["extra"] = [{"sid": sid, "title": (recs.get(sid) or {}).get("title") or e.get("title") or ""}
                         for sid, e in (snap.get("sessions") or {}).items() if sid not in self._map[3] and sid in recs]
        self._apply_app_state(snap, recs)
        self._apply_host(snap, self.mapping())
        # 판정 행인데 앱 기록에 없거나(삭제) 보관된 세션 → 페이지·트레이가 뺀다. 기록을 못 읽으면(빈 결과) 아무것도 빼지 않는다
        snap["gone"] = [sid for sid in self._map[3] if sid not in recs or recs[sid]["archived"]] if recs else []
        return snap

    @staticmethod
    def _apply_app_state(snap, recs):
        """앱 자체 판정으로 질문 대기를 바로잡는다.
        - 글로 추정한 질문(kind text): 앱이 completed 면 질문 아님(쉼), blocked 면 질문, 판정 없으면 weak(띠만, 열은 안 옮김)
        - 질문 문장이 없어도 앱이 blocked 로 본 쉬는 세션 → 질문 대기(kind app, 글 = 앱이 적은 할 일)
        - 다음 단계 질문·AskUserQuestion(확정)은 건드리지 않는다. 도는 세션도 그대로
        - 앱 판정이 대화 파일의 마지막 턴 것이 아니면(상황판이 직접 돌린 턴 등) 판정이 없는 것으로 친다(11-18: 며칠 전
          「확인 필요」 판정이 남아, 상황판에서 「종료해 주세요」로 끝낸 세션이 내 답 대기에 묶였다)"""
        for sid, e in (snap.get("sessions") or {}).items():
            rec = recs.get(sid) or {}
            stale = bool(e.get("auid") and rec.get("pts_for") and e["auid"] != rec["pts_for"])
            st = None if stale else rec.get("app_state")
            if rec.get("detail") and not stale:
                e["app_detail"] = rec["detail"]
            ask = e.get("ask")
            if e.get("state") == "asking" and ask and ask.get("kind") == "text":
                if st == "completed":
                    e.update(state="idle", label="턴 끝남", ask=None)
                elif st != "blocked":
                    ask["weak"] = True
            elif e.get("state") == "idle" and st == "blocked":
                need = (recs.get(sid) or {}).get("needs") or "작업자 확인 필요"
                e.update(state="asking", label="질문(앱 판단): " + need, ask={"text": need, "options": [], "app": [], "kind": "app"})
        for u in snap.get("unmapped") or []:  # 앱 기록과 짝이 없으면 판정할 길이 없으니 추정 질문은 weak
            if u.get("state") == "asking" and (u.get("ask") or {}).get("kind") == "text":
                u["ask"]["weak"] = True

    def _mark(self, sid, status):
        """판정 행 하나의 열을 작업자가 직접 바꾼다. judged_at 은 지금, marked_at 은 그 시각(초) — 페이지·트레이는
        세션이 marked_at 뒤에 다시 움직이기 전까지 이 열을 따른다. 바꾼 뒤 상황판 HTML 을 다시 그린다."""
        src = self.root / "board-sessions.json"
        with _MARK_LOCK:
            rows = json.loads(src.read_text(encoding="utf-8"))
            hit = False
            for r in rows:
                if isinstance(r, dict) and str(r.get("link", "")).endswith(sid) and r.get("status") != "관제":
                    r.update(status=status, judged_at=time.strftime("%m-%d %H:%M"), marked_at=int(time.time()))
                    hit = True
            if not hit:
                return False
            tmp = src.with_name(src.name + ".tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(src)
            import importlib.util
            spec = importlib.util.spec_from_file_location("board_render", Path(__file__).with_name("board-render.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            try:
                mod.main(str(src), str(self.root / "session-board.html"))
            except SystemExit as e:
                if e.code not in (0, None):
                    raise RuntimeError(f"다시 그리기 실패: {e.code}")
        return True

    def _stream(self):
        """SSE: 스냅숏이 바뀌면(또는 15초마다 생존 신호) 보낸다. 브라우저가 연결을 끊으면 끝."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last, last_sent = None, 0.0
        try:
            while True:
                snap = json.dumps(self._live(), ensure_ascii=False)
                key = snap.split('"now"', 1)[0] + snap.split('"ready"', 1)[-1]  # 시계만 바뀐 건 같은 것으로
                now = time.time()
                if key != last or now - last_sent > 15:
                    self.wfile.write(b"data: " + snap.encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                    last, last_sent = key, now
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/pair":  # 트레이가 연 창: 한 번 쓰는 코드 → 입력 열쇠 쿠키, 그리고 상황판으로
            qs = parse_qs(u.query)
            ok = _take_pair(qs.get("c", [""])[0])
            sid = qs.get("s", [""])[0]
            self.send_response(302)
            if ok:
                self.send_header("Set-Cookie", f"{INPUT_COOKIE}={INPUT_KEY}; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("Location", "/session-board.html" + (f"#s={sid}" if SID.match(sid) else ""))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if u.path == "/api/health":
            return self._json(200, {
                "ok": True,
                "armed": (self.root / ".watcher-armed").exists(),
                "queued": len(list((self.root / "inbox").glob("*.json"))),
            })
        if u.path == "/api/status":
            rid = parse_qs(u.query).get("id", [""])[0]
            if not RID.match(rid):
                return self._json(400, {"error": "bad id"})
            for state in STATES:
                p = self.root / state / (rid + ".json")
                if p.exists():
                    if state == "done":
                        d = json.loads(p.read_text(encoding="utf-8"))
                        d.setdefault("state", "done")
                        return self._json(200, {"state": d["state"], "result": d.get("result", ""), "finished_at": d.get("finished_at", "")})
                    return self._json(200, {"state": "queued" if state == "inbox" else "working"})
            return self._json(404, {"state": "unknown"})
        if u.path in ("/api/live", "/api/stream", "/api/timeline", "/api/heatmap", "/api/transcript", "/api/host/state", "/api/host/list",
                      "/api/host/paired", "/api/doc", "/api/doc/list", "/api/doc/recent"):
            qs = parse_qs(u.query)
            if not (self._auth(qs.get("t", [""])[0]) or self._auth()):  # 쿼리 t 또는 X-Board-Token 헤더
                return self._json(403, {"error": "token"})
            if u.path == "/api/transcript":
                return self._transcript(qs)
            if u.path.startswith("/api/doc"):
                return self._doc(u.path, qs)
            if u.path == "/api/host/paired":  # 이 창이 직접 돌리는 세션에 입력을 보낼 수 있나(트레이가 연 창인가)
                return self._json(200, {"paired": self._input_ok(), "cli": bool(board_host.find_cli()), "os": os.name})
            if u.path == "/api/host/list":
                return self._json(200, {"cli": bool(board_host.find_cli()), "sessions": self.host_mgr().list()})
            if u.path == "/api/host/state":
                cli = qs.get("cli", [""])[0]
                sid = qs.get("sid", [""])[0]
                if not cli and SID.match(sid):
                    p = self.transcript_path(sid=sid)
                    cli = p.stem if p else ""
                h = self.host_mgr().get(cli) if cli else None
                st = h.state() if h else {"alive": False, "busy": False, "cli": cli, "pending": []}
                st["cli_installed"] = bool(board_host.find_cli())
                st["app_holds"] = bool(cli) and not (h and h.alive()) and board_host.held_by_app(cli)
                st["released"] = bool(cli) and not (h and h.alive()) and cli in self.host_mgr().released
                return self._json(200, st)
            if u.path == "/api/timeline":
                try:
                    hours = max(1, min(24 * 7, int(qs.get("hours", ["6"])[0])))
                except ValueError:
                    hours = 6
                return self._json(200, self.tracker.timeline(self.mapping(), self.names(), hours) if self.tracker else {})
            if u.path == "/api/heatmap":
                return self._json(200, self.tracker.heatmap(7) if self.tracker else {})
            return self._live_reply() if u.path == "/api/live" else self._stream()
        if u.path == "/":
            self.path = "/session-board.html"
            u = urlparse(self.path)
        if u.path not in STATIC:
            return self._json(404, {"error": "not found"})
        return super().do_GET()

    def _live_reply(self):
        return self._json(200, self._live())

    def transcript_path(self, sid=None, cli=None):
        """사이드바 세션 id(또는 대화 id) → 그 세션의 대화 파일(가장 최근에 쓰인 것). 앱 기록 · 판정 짝 · 대화 폴더 순으로 찾는다."""
        if cli:
            clis = {cli} if board_host.UUID_RE.match(cli) else set()
        else:
            clis = set((self.app_records().get(sid) or {}).get("cli") or [])
            clis |= {c for c, s in self.mapping().items() if s == sid}
        ws = board_live.watched_workspace(self.root)
        found = [d / f"{c}.jsonl" for d in board_live.project_dirs(ws) for c in clis]
        found = [p for p in found if p.exists()]
        return max(found, key=lambda p: p.stat().st_mtime) if found else None

    hosts = None  # board_host.HostManager — 상황판이 직접 돌리는 CLI 세션들(처음 쓸 때 만든다)

    @classmethod
    def host_mgr(cls):
        if cls.hosts is None:
            cls.hosts = board_host.HostManager()
        return cls.hosts

    def _host_target(self, body):
        """보낼 세션 → (대화 id, 작업 폴더, 이어받기 여부, 모델, 노력, 권한 모드). new 면 감시 폴더에서 새 대화."""
        ws = str(board_live.watched_workspace(self.root))
        if body.get("new"):
            return self.host_mgr().new_id(), ws, False, None, None, None
        sid, cli = str(body.get("sid") or ""), str(body.get("cli") or "")
        if sid and not SID.match(sid):
            raise ValueError("bad sid")
        path = self.transcript_path(sid=sid) if sid else self.transcript_path(cli=cli)
        if not path:
            raise FileNotFoundError("이 세션의 대화 파일을 못 찾았어요")
        rec = (self.app_records().get(sid) or {}) if sid else {}
        return path.stem, board_docs.transcript_cwd(path) or ws, True, rec.get("model") or None, rec.get("effort") or None, rec.get("mode") or None

    def _host_post(self, path, body):
        """상황판이 직접 돌리는 세션: send(보내기 · 새 세션) / answer(허용 · 거부 · 선택지 답) / stop(멈추기)."""
        mgr = self.host_mgr()
        try:
            if path == "/api/host/send":
                text = str(body.get("text", "")).strip()
                if not text or len(text) > 20000:
                    return self._json(400, {"error": "보낼 글이 없거나 너무 깁니다"})
                cli, cwd, resume, model, effort, mode = self._host_target(body)
                h = mgr.ensure(cli, cwd, resume, model, effort, mode)
                if h.busy:
                    return self._json(409, {"error": "세션이 아직 답하는 중이에요. 끝나면 보내거나 「멈추기」를 누르세요"})
                h.send(text)
                return self._json(200, {"ok": True, "cli": cli})
            cli = str(body.get("cli", ""))
            h = mgr.get(cli)
            if not h or not h.alive():
                return self._json(404, {"error": "상황판이 돌리는 세션이 아니에요(꺼졌으면 다시 보내면 이어 받아요)"})
            if path == "/api/host/answer":
                beh = str(body.get("behavior", ""))
                if beh not in ("allow", "deny"):
                    return self._json(400, {"error": "behavior"})
                answers = body.get("answers") if isinstance(body.get("answers"), dict) else None
                h.answer(str(body.get("request_id", "")), beh, str(body.get("message", ""))[:500], answers)
                return self._json(200, {"ok": True})
            if path == "/api/host/stop":
                h.interrupt()
                return self._json(200, {"ok": True})
        except PermissionError as e:
            return self._json(409, {"error": str(e)})
        except (FileNotFoundError, KeyError, ValueError) as e:
            return self._json(400, {"error": str(e).strip("'")})
        except RuntimeError as e:
            return self._json(500, {"error": str(e)})
        except OSError as e:
            return self._json(500, {"error": f"세션 프로세스 오류: {e}"})
        return self._json(404, {"error": "not found"})

    @staticmethod
    def _apply_host(snap, mapping):
        """상황판이 돌리는 세션이 허용 · 답을 기다리면 카드에 「내 답 대기」로(앱 판정과 별개, 이쪽이 확실)."""
        mgr = Handler.hosts
        if not mgr:
            return
        by_cli = {u.get("cli"): u for u in snap.get("unmapped") or []}
        for st in mgr.list():
            e = (snap.get("sessions") or {}).get(mapping.get(st["cli"], "")) or by_cli.get(st["cli"])
            if not e:
                continue
            e["hosted"] = {"busy": st["busy"], "pending": len(st["pending"]), "mode": st["mode"]}
            if st["pending"]:
                p = st["pending"][0]
                text = ("질문: " + str((p.get("questions") or [{}])[0].get("question", ""))) if p["kind"] == "question" else ("허용 요청: " + p["tool"])
                e.update(state="asking", label=text, ask={"text": text, "options": [], "app": [], "kind": "host"})

    def _transcript(self, qs):
        """GET /api/transcript?sid=…[&before=바이트|&after=바이트] → 상세 창의 「대화」(읽기 전용)."""
        sid, cli = qs.get("sid", [""])[0], qs.get("cli", [""])[0]
        if not SID.match(sid) and not board_host.UUID_RE.match(cli):
            return self._json(400, {"error": "bad sid"})
        path = self.transcript_path(sid=sid) if SID.match(sid) else self.transcript_path(cli=cli)
        if not path:
            return self._json(404, {"error": "대화 파일을 못 찾음"})

        def num(k):
            try:
                v = qs.get(k, [""])[0]
                return int(v) if v != "" else None
            except ValueError:
                return None
        try:
            out = board_live.transcript(path, before=num("before"), after=num("after"))
        except OSError as e:
            return self._json(500, {"error": str(e)})
        out["file"] = path.stem
        return self._json(200, out)

    def _doc(self, path, qs):
        """상황판 「문서」(md 읽기 전용, board_docs):
        GET /api/doc?path=…[&sid=…&base=…|&wiki=이름][&ver=…] → 문서 하나(ver 가 같으면 {unchanged})
        GET /api/doc/list?sid=…  → 그 세션이 쓰고 · 고치고 · 읽고 · 말한 md 파일
        GET /api/doc/recent      → 감시 폴더 · 메모리 폴더에서 최근에 바뀐 md 파일"""
        ws = board_live.watched_workspace(self.root)

        def q(k):
            return qs.get(k, [""])[0]
        try:
            if path == "/api/doc/recent":
                return self._json(200, board_docs.recent(ws))
            sid = q("sid")
            if sid and not SID.match(sid):
                return self._json(400, {"error": "bad sid"})
            tp = self.transcript_path(sid=sid) if sid else None
            if path == "/api/doc/list":
                if not tp:
                    return self._json(404 if sid else 400, {"error": "대화 파일을 못 찾음" if sid else "sid 필요"})
                return self._json(200, {"items": board_docs.touched(tp, ws)})
            allowed = board_docs.roots(ws)
            bases = []
            if q("base"):  # 보던 문서의 폴더(문서 속 상대 링크)
                try:
                    b = Path(q("base")).resolve()
                    if board_docs.inside(b, allowed):
                        bases.append(str(b))
                except OSError:
                    pass
            if tp:  # 그 세션의 작업 폴더(대화 속 상대 경로)
                c = board_docs.transcript_cwd(tp)
                if c:
                    bases.append(c)
            bases.append(str(ws))
            p = board_docs.resolve_wiki(q("wiki"), allowed, bases) if q("wiki") else board_docs.resolve(q("path"), allowed, bases)
            if not p:
                return self._json(404, {"error": "감시 폴더 안에 있는 md 파일만 열 수 있어요(파일이 없거나 폴더 밖)"})
            if q("ver") and q("ver") == board_docs.version(p):
                return self._json(200, {"unchanged": True})
            return self._json(200, board_docs.read(p, ws))
        except OSError as e:
            return self._json(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        if not self._auth():
            return self._json(403, {"error": "token"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            return self._json(413, {"error": "too big"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})
        if u.path.startswith("/api/host/"):
            if not self._input_ok():  # 토큰만으로는 안 됨(파일에 적혀 있어 다른 세션도 읽을 수 있다) — 트레이가 연 창의 쿠키가 있어야
                return self._json(403, {"error": "트레이 아이콘으로 연 상황판 창에서만 보낼 수 있어요(입력 열쇠 없음)"})
            return self._host_post(u.path, body)
        if u.path == "/api/open":
            # Claude 앱에서 그 세션 열기. Edge 앱 창(--app)은 claude:// 링크를 앱에 넘기지 못해 「↗」를 눌러도 반응이 없었다
            # (11-11 실측: 같은 링크를 Windows 로 열면 앱이 그 세션으로 바뀜). 그래서 서버(트레이 프로세스)가 대신 연다
            sid = str(body.get("session_id", ""))
            if not SID.match(sid):
                return self._json(400, {"error": "bad session_id"})
            url = "claude://claude.ai/epitaxy/" + sid
            try:
                if os.name == "nt":
                    os.startfile(url)
                else:
                    import subprocess
                    subprocess.Popen(["open", url])
            except OSError as e:
                return self._json(500, {"error": str(e)})
            return self._json(200, {"ok": True})
        if u.path == "/api/mark":
            # 카드·상세 창의 상태 드롭다운: 작업자가 직접 열을 옮긴다(관제를 거치지 않음, 11-18 버튼 → 11-19 드롭다운).
            # 판정 행만 바꾸고 다시 그린다. 세션이 그 뒤 다시 움직이면 실시간 상태가 이긴다(marked_at)
            sid = str(body.get("session_id", ""))
            status = str(body.get("status", ""))
            if not SID.match(sid) or status not in MARKABLE:
                return self._json(400, {"error": "bad request"})
            try:
                hit = self._mark(sid, status)
            except Exception as e:
                return self._json(500, {"error": str(e)[:200]})
            return self._json(200 if hit else 404, {"ok": hit} if hit else {"error": "상황판에 그 세션 행이 없어요"})
        if u.path == "/api/summarize":
            sid = str(body.get("session_id", ""))
            if not SID.match(sid):
                return self._json(400, {"error": "bad session_id"})
            req = {"type": "summarize", "session_id": sid, "session": str(body.get("session", ""))[:200]}
        elif u.path in ("/api/archive", "/api/delete"):
            ids = body.get("session_ids") or []
            titles = body.get("sessions") or []
            limit = 25 if u.path == "/api/delete" else 100  # delete_session 은 한 번에 25개까지
            if not isinstance(ids, list) or not ids or len(ids) > limit or not all(isinstance(x, str) and SID.match(x) for x in ids):
                return self._json(400, {"error": "bad session_ids"})
            req = {"type": u.path.rsplit("/", 1)[-1], "session_ids": ids, "sessions": [str(t)[:200] for t in titles][:limit]}
        elif u.path == "/api/map":
            cli = str(body.get("cli", ""))
            if not CLI.match(cli):
                return self._json(400, {"error": "bad cli"})
            req = {"type": "map", "cli": cli, "title": str(body.get("title", ""))[:200]}
        elif u.path == "/api/message":
            sid = str(body.get("session_id", ""))
            text = str(body.get("text", "")).strip()
            if not SID.match(sid):
                return self._json(400, {"error": "bad session_id"})
            if not text or len(text) > MAX_TEXT:
                return self._json(400, {"error": f"text 1~{MAX_TEXT}자"})
            req = {"type": "message", "session_id": sid, "session": str(body.get("session", ""))[:200], "text": text}
        else:
            return self._json(404, {"error": "not found"})
        rid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        req.update({"id": rid, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        inbox = self.root / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        tmp = inbox / (rid + ".json.tmp")  # 쓰다 만 파일을 watcher 가 집지 않게 .tmp 로 쓰고 이름을 바꾼다
        tmp.write_text(json.dumps(req, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(inbox / (rid + ".json"))
        return self._json(200, {"id": rid, "state": "queued"})


class Server(ThreadingHTTPServer):
    """127.0.0.1 전용 서버. 포트를 혼자 잡는다 — 파이썬 기본값(SO_REUSEADDR)은 Windows 에서 다른 프로그램이 쓰는 포트에도
    그대로 묶여 '사용 중'을 못 알아채므로(11-11 실측: 이미 떠 있는 서버 포트에 두 번째 서버가 묶임) 끄고 SO_EXCLUSIVEADDRUSE 를 건다.
    macOS · Linux 의 SO_REUSEADDR 는 듣고 있는 포트에 겹쳐 묶지 못하고 TIME_WAIT 만 넘게 해 주므로 켜 둔다(파이썬 기본값).
    끄면 재시작 직후 30초쯤 '사용 중'이 나 상주가 포트를 다른 번호로 옮겨 버린다."""
    daemon_threads = True  # SSE 연결이 남아 있어도 서버를 끌 수 있게
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if os.name == "nt":
            super().server_bind()
            return
        # macOS · Linux: HTTPServer.server_bind 가 부르는 socket.getfqdn(127.0.0.1 역방향 DNS 조회)이 30초 넘게 멈추는 환경이 있다
        # (11-11 GitHub macOS 러너 실측 — 서버가 안 뜸). server_name 은 쓰지 않으므로 조회 없이 묶는다. Windows 는 위 그대로
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="board")
    ap.add_argument("--port", type=int, default=0, help="기본: board/config.json 의 port, 없으면 18799")
    ap.add_argument("--pair", type=int, default=0, metavar="초", help="개발용: 입력 연결 주소를 찍는다(이 초 안에 한 번)")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if not a.port:
        try:
            a.port = int(json.loads((root / "config.json").read_text(encoding="utf-8")).get("port") or 18799)
        except (OSError, ValueError, TypeError, AttributeError):
            a.port = 18799
    if not (root / "session-board.html").exists():
        sys.exit(f"{root} 에 session-board.html 이 없습니다. 먼저 /board 로 상황판을 만드세요")
    for d in STATES:
        (root / d).mkdir(exist_ok=True)
    Handler.root = root
    ws = board_live.watched_workspace(root)  # board/config.json 의 workspace, 없으면 board 의 부모
    Handler.app = AppSessions(ws)
    # ① 실시간 층: 작업 폴더의 대화 파일을 이어 읽는다. 최근 7일 파일만.
    Handler.tracker = board_live.Tracker(ws, days=7, interval=1.5)
    Handler.tracker.start()
    srv = Server(("127.0.0.1", a.port), Handler)
    print(f"board-server: http://127.0.0.1:{a.port}/  root={root}", flush=True)
    if a.pair:  # 개발용: 입력 열쇠를 받을 창 주소(60초 · 한 번). 평소엔 트레이가 창을 열며 넘긴다
        print(f"board-server: 입력 연결 http://127.0.0.1:{a.port}/pair?c={pair_code(a.pair)}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
