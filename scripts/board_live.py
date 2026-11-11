"""board_live.py — Claude Code 대화 파일(jsonl)을 이어 읽어 세션별 실시간 상태·토큰을 만든다.

상황판 ① 실시간 층(11-10). LLM 을 거치지 않는다.
- 데스크톱 앱 세션도 ~/.claude/projects/<작업 폴더 슬러그>[워크트리 접미사]/<cli 세션 id>.jsonl 에
  한 줄씩 실시간으로 쓴다(entrypoint: claude-desktop). 줄마다 type·timestamp, assistant 줄엔
  message.usage(토큰)와 stop_reason(tool_use/end_turn)이 있다.
- 파일 이름(cli id)과 사이드바 세션 id(local_…)는 다르다. 짝은 board-map.py 가 판정 때 만들어
  board-sessions.json 행의 cli_id 에 저장하고, 서버는 그걸 읽어 쓴다.

board-server.py 가 Tracker 를 백그라운드 스레드로 돌리고, board-map.py 가 timestamps() 를 쓴다.
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
PROJECTS = Path.home() / ".claude" / "projects"
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
TS_RE = re.compile(rb'"timestamp":"([0-9T:\.\-]+Z)"')
SID_RE = re.compile(r"local_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# 세션 사이 연결로 보는 도구 호출(11-10 실측: SendMessage 256건·start_session 16건/7일)
SEND_TOOLS = ("SendMessage", "mcp__ccd_session_mgmt__send_message")
SPAWN_TOOLS = ("mcp__ccd_session__start_session", "mcp__ccd_session__hand_off_to_session")
# 작업자에게 질문하고 끝낸 턴: 다음 단계 상자의 질문(wrap_up.ask)과 AskUserQuestion
# 사용자 줄이지만 작업자 답이 아닌 것(시스템이 넣는 알림). 이런 줄은 질문 대기를 풀지 않는다
# 백그라운드 작업. 턴을 끝내고 백그라운드 명령 결과를 기다리는 세션은 도는 중으로 본다
BG_TOOLS = ("Bash", "PowerShell")
BG_START_RE = re.compile(r"running in background with ID: ([A-Za-z0-9_-]+)")
BG_NOTE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)  # 끝남 알림 한 덩어리
BG_FIELD_RE = {k: re.compile(rf"<{k}>(.*?)</{k}>", re.S) for k in ("task-id", "status", "summary")}
BG_WINDOW = 90 * 60     # 완료 알림 없이 사라진 작업(앱 재시작·중단)은 이만큼 지나면 무시
BG_IGNORE = ("board-queue.py wait",)  # 관제 세션이 늘 걸어 두는 요청 대기는 일이 아니다
# 마지막 문장이 이런 꼴이면 작업자에게 묻고 멈춘 것으로 추정(물음표 없이 "…답해 주시면 바로 커밋하겠습니다." 같은 경우, 11-10 실측)
ASK_TAIL_RE = re.compile(r"[?？]$|(답해|알려|골라|정해|확인해|말씀해) ?(주시면|주세요|주십시오)")
# 다른 세션(관제 포함)이 send_message 로 보낸 말. 대화 파일엔 isMeta 사용자 줄로 남는다
RELAY_MARK = "<cross-session-message"
BOARD_RELAY = "상황판에서 작업자가 직접 보낸 메시지입니다"  # 관제가 상황판 채팅을 전달할 때 붙이는 첫 줄 = 작업자 본인의 말
SYSTEM_PROMPT_PREFIX = ("[SYSTEM NOTIFICATION", "<task-notification", "<side-sessions-event", "<system-reminder", "<ci-monitor-event", "<local-command", "<command-")
EDGE_WINDOW = 48 * 3600
SEG_GAP = 120          # 기록 사이가 2분 넘게 비면 활동 구간을 끊는다(타임라인 막대)
KEEP = 8 * 86400       # 구간·토큰 시각은 8일치만 들고 있는다


def slug(path) -> str:
    """작업 폴더 경로 → projects 폴더 이름(영숫자 외 문자는 모두 '-')."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def watched_workspace(root) -> Path:
    """감시할 작업 폴더. board/config.json 의 "workspace"(팀 배포 설치: 도구 폴더와 작업 폴더가 따로),
    없으면 board 폴더의 부모(도구를 작업 폴더 안에 둔 경우)."""
    root = Path(root)
    try:
        ws = json.loads((root / "config.json").read_text(encoding="utf-8")).get("workspace")
        if ws:
            return Path(ws)
    except (OSError, ValueError, AttributeError):
        pass
    return root.parent


def project_dirs(workspace):
    """작업 폴더와 그 워크트리들의 대화 폴더."""
    s = slug(Path(workspace).resolve())
    return sorted(d for d in PROJECTS.glob(s + "*") if d.is_dir())


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def timestamps(path: Path):
    """파일 안의 모든 timestamp(초, float) — 짝짓기용. json 을 풀지 않고 정규식으로 빠르게."""
    out = []
    with open(path, "rb") as f:
        for m in TS_RE.finditer(f.read()):
            t = parse_ts(m.group(1).decode())
            if t:
                out.append(t)
    return out


def _first_line(text, n=140):
    for line in str(text).splitlines():
        line = line.strip()
        if line:
            return line[:n]
    return ""


class FileState:
    """jsonl 파일 하나의 누적 상태."""

    def __init__(self, path: Path):
        self.path = path
        self.cli = path.stem
        self.offset = 0
        self.tail = b""
        self.last_ts = None          # 마지막 줄 시각(epoch)
        self.last_main = None        # 본 대화(사이드체인 제외) 마지막 user/assistant 줄 요약
        self.last_text = ""          # 마지막 assistant 글 첫 줄
        self.model = ""
        self.usage = {}              # message.id → (epoch, {토큰})  같은 메시지가 여러 줄로 나뉘어도 한 번만 센다
        self.segs = []               # 활동 구간 [[시작, 끝], …] (타임라인·히트맵용)
        self.cache = None            # (version, sums)
        self.version = 0
        self.last_end_ts = None      # 본 대화의 마지막 턴 종료(end_turn) 시각
        self.events = []             # 이 세션이 다른 세션에 보낸 메시지·띄운 세션 {kind, to, summary, ts}
        self.spawn_wait = {}         # start_session 호출 id → 이벤트 (결과에서 새 세션 id 를 채움)
        self.bg = {}                 # 아직 안 끝난 백그라운드 작업 id → {ts, desc, cmd}
        self.bg_cmd = {}             # run_in_background 도구 호출 id → {desc, cmd}(결과에서 작업 id 를 받는다)
        self.bg_done = []            # 최근 끝난 작업 {id, desc, status, summary, ts}(최대 6개)
        self.user_ts = None          # 작업자가 직접 친 마지막 말(시스템 알림 제외) 시각 — 판정 뒤 답했는지 보려고
        self.title = ""              # 사이드바 제목(앱이 type "custom-title" 줄로 쓴다). 판정 전 새 세션 카드 이름으로 쓴다
        self.ask = None              # 작업자에게 남긴 질문 {text, options, kind, ts, id} — 작업자가 답하면(사용자 줄) 지운다
        self.last_tail_text = ""     # 마지막 assistant 글의 마지막 줄(물음표로 끝나면 질문으로 추정)

    def feed(self, raw: bytes):
        data = self.tail + raw
        lines = data.split(b"\n")
        self.tail = lines.pop()      # 아직 줄바꿈이 안 온 마지막 조각
        for line in lines:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            self._line(d)
        self.version += 1

    def _line(self, d):
        ts = parse_ts(d.get("timestamp", "")) if d.get("timestamp") else None
        if ts:
            self.last_ts = ts
            if self.segs and 0 <= ts - self.segs[-1][1] <= SEG_GAP:
                self.segs[-1][1] = max(self.segs[-1][1], ts)
            elif not self.segs or ts > self.segs[-1][1]:
                self.segs.append([ts, ts])
                if len(self.segs) > 4000:
                    cut = ts - KEEP
                    self.segs = [x for x in self.segs if x[1] >= cut]
        typ = d.get("type")
        # 일하는 도중 도착한 백그라운드 끝남 알림은 사용자 줄이 아니라 대기열 기록(queue-operation enqueue)·첨부(queued_command)에 남는다
        if typ == "queue-operation" and d.get("operation") == "enqueue" and "<task-notification>" in str(d.get("content") or ""):
            self._bg_notes(str(d["content"]), ts)
            return
        if typ == "attachment" and isinstance(d.get("attachment"), dict) and d["attachment"].get("type") == "queued_command":
            self._bg_notes(str(d["attachment"].get("prompt") or ""), ts)
            return
        if typ == "custom-title" and d.get("customTitle"):
            self.title = str(d["customTitle"])[:120]
            return
        if typ not in ("user", "assistant"):
            return
        m = d.get("message") or {}
        if typ == "assistant":
            u = m.get("usage")
            mid = m.get("id") or d.get("requestId") or d.get("uuid")
            if u and mid:
                self.usage[mid] = (ts or time.time(), {k: int(u.get(k) or 0) for k in TOKEN_KEYS})
            if m.get("model"):
                self.model = m["model"]
        if d.get("isSidechain"):
            return
        if d.get("isMeta"):
            # 다른 세션이 보낸 말은 새 입력으로 친다: 남아 있던 질문은 새 턴에 묻혀 지운다(다시 물으면 다시 잡힘).
            # 상황판에서 작업자가 보낸 말이면 작업자 답으로도 친다(11-10: 상황판 선택지 버튼으로 답한 세션이 계속 질문 대기로 남던 문제)
            if typ == "user":
                c = m.get("content")
                text = c if isinstance(c, str) else " ".join(b.get("text", "") for b in c if isinstance(b, dict)) if isinstance(c, list) else ""
                if RELAY_MARK in text:
                    self.ask = None
                    self.last_tail_text = ""
                    if BOARD_RELAY in text:
                        self.user_ts = ts or self.user_ts
                    self.last_main = {"kind": "prompt", "stop": None, "tools": [], "ts": ts}
            return
        content = m.get("content")
        if typ == "assistant":
            tools, texts = [], []
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "tool_use":
                    name = b.get("name", "")
                    tools.append(name)
                    inp = b.get("input") or {}
                    if name in SEND_TOOLS:
                        to = inp.get("to") or inp.get("recipient") or inp.get("session_id") or ""
                        summ = inp.get("summary") or _first_line(inp.get("message") or inp.get("content") or "", 60)
                        self.events.append({"kind": "msg", "to": str(to), "summary": str(summ)[:80], "ts": ts})
                    elif name in BG_TOOLS and inp.get("run_in_background") and b.get("id"):
                        self.bg_cmd[b["id"]] = {"cmd": str(inp.get("command") or ""), "desc": str(inp.get("description") or "")[:120]}
                    elif name == "TaskStop":
                        self._bg_end(str(inp.get("task_id") or inp.get("shell_id") or ""), "stopped", "TaskStop 으로 중단", ts)
                    elif name == "mcp__ccd_turn__wrap_up" and inp.get("ask"):
                        acts = [a for a in (inp.get("actions") or []) if isinstance(a, dict) and (a.get("label") or a.get("action"))][:3]
                        # app = 앱 다음 단계 상자의 앱 동작(commit·create_pr 등). 글로 보내서는 실행되지 않는다
                        self.ask = {"text": str(inp["ask"])[:200], "options": [a.get("label") or a.get("action") for a in acts],
                                    "app": [a.get("label") or a.get("action") for a in acts if a.get("action")], "kind": "wrap_up", "ts": ts, "id": b.get("id")}
                    elif name == "AskUserQuestion":
                        qs = inp.get("questions") or []
                        q = qs[0] if qs and isinstance(qs[0], dict) else {}
                        opts = [o.get("label", "") for o in (q.get("options") or []) if isinstance(o, dict)]
                        self.ask = {"text": str(q.get("question") or "질문")[:200], "options": [o for o in opts if o][:4], "kind": "ask_user", "ts": ts, "id": b.get("id")}
                    elif name in SPAWN_TOOLS:
                        ev = {"kind": "spawn", "to": "", "summary": str(inp.get("title") or "")[:80], "ts": ts, "name": inp.get("name") or ""}
                        if b.get("id"):
                            self.spawn_wait[b["id"]] = ev
                        self.events.append(ev)
                elif b.get("type") == "text" and b.get("text"):
                    texts.append(b["text"])
            if texts:
                self.last_text = _first_line(texts[-1])
                tail = [x.strip() for x in texts[-1].splitlines() if x.strip()]
                self.last_tail_text = tail[-1][:200] if tail else ""
            self.last_main = {"kind": "assistant", "stop": m.get("stop_reason"), "tools": tools, "ts": ts}
            if m.get("stop_reason") == "end_turn":
                self.last_end_ts = ts
        else:
            # start_session 결과에서 새 세션 id 를 찾아 띄우기 이벤트에 채운다
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in self.spawn_wait:
                    found = SID_RE.search(json.dumps(b.get("content"), ensure_ascii=False))
                    if found:
                        self.spawn_wait.pop(b["tool_use_id"])["to"] = found.group(0)
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                kind = "tool_result"
                for b in content:  # 백그라운드로 띄운 명령의 작업 id
                    if isinstance(b, dict) and b.get("tool_use_id") in self.bg_cmd:
                        c = self.bg_cmd.pop(b["tool_use_id"])
                        found = BG_START_RE.search(json.dumps(b.get("content"), ensure_ascii=False))
                        if found and not any(x in c["cmd"] for x in BG_IGNORE):
                            self.bg[found.group(1)] = {"ts": ts or time.time(), "desc": c["desc"] or _first_line(c["cmd"], 80), "cmd": c["cmd"][:200]}
                # AskUserQuestion 에 작업자가 답하면 그 결과가 tool_result 로 온다
                if self.ask and self.ask["kind"] == "ask_user" and any(isinstance(b, dict) and b.get("tool_use_id") == self.ask["id"] for b in content):
                    self.ask = None
            else:
                kind = "prompt"
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                else:
                    text = ""
                self._bg_notes(text, ts)  # 백그라운드 작업 끝남 알림
                if not text.lstrip().startswith(SYSTEM_PROMPT_PREFIX) and "<task-notification>" not in text:
                    self.user_ts = ts or self.user_ts
                    self.ask = None          # 작업자가 답함
                    self.last_tail_text = ""
            self.last_main = {"kind": kind, "stop": None, "tools": [], "ts": ts}

    def sums(self, today: str):
        if self.cache and self.cache[0] == (self.version, today):
            return self.cache[1]
        total = dict.fromkeys(TOKEN_KEYS, 0)
        day = dict.fromkeys(TOKEN_KEYS, 0)
        for t, u in self.usage.values():
            d = datetime.fromtimestamp(t, KST).date().isoformat()
            for k in TOKEN_KEYS:
                total[k] += u[k]
                if d == today:
                    day[k] += u[k]
        self.cache = ((self.version, today), (total, day))
        return total, day

    def _bg_notes(self, text, ts):
        for blk in BG_NOTE_RE.findall(text):
            f = {k: (r.search(blk).group(1).strip() if r.search(blk) else "") for k, r in BG_FIELD_RE.items()}
            self._bg_end(f["task-id"], f["status"] or "completed", f["summary"], ts)

    def _bg_end(self, tid, status, summary, ts):
        t = self.bg.pop(tid, None)
        if t is None:
            return
        self.bg_done = (self.bg_done + [{"id": tid, "desc": t["desc"], "status": status, "summary": summary[:160], "ts": ts or time.time()}])[-6:]

    def background(self, now):
        """상세 창용: 아직 안 끝난 작업(창 안) + 한 시간 안에 끝난 작업."""
        run = [{"id": k, "desc": v["desc"], "age": int(now - v["ts"]), "at": datetime.fromtimestamp(v["ts"], KST).strftime("%H:%M")}
               for k, v in self.bg.items() if now - v["ts"] < BG_WINDOW]
        done = [{"id": d["id"], "desc": d["desc"], "status": d["status"], "summary": d["summary"], "ago": int(now - d["ts"])}
                for d in self.bg_done if now - d["ts"] < 3600]
        return sorted(run, key=lambda x: x["age"]), done[::-1]

    def question(self):
        """턴이 작업자에게 묻고 끝났으면 {text, options, kind}. wrap_up.ask·AskUserQuestion 은 확정, 마지막 줄이 물음표면 추정(kind=text)."""
        lm = self.last_main
        if not lm or lm["kind"] != "assistant":
            return None
        if self.ask and lm["stop"] == "tool_use" and self.ask["kind"] == "ask_user" and "AskUserQuestion" in lm["tools"]:
            return self.ask
        if lm["stop"] not in ("end_turn", "stop_sequence"):
            return None
        if self.ask:
            return self.ask
        t = self.last_tail_text.rstrip(" *_`)")
        if ASK_TAIL_RE.search(t):
            return {"text": t, "options": [], "kind": "text"}
        return None

    def state(self, now: float):
        """working(돌고 있음) / waiting_tool(도구 호출 뒤 응답 없음 — 승인 대기이거나 긴 도구) / asking(작업자에게 묻고 멈춤) / idle."""
        age = now - (self.last_ts or 0)
        lm = self.last_main
        if not lm:
            return "idle", "", age
        tool = lm["tools"][-1] if lm["tools"] else ""
        q = self.question()
        if q:
            return "asking", ("질문(추정): " if q["kind"] == "text" else "질문: ") + q["text"], age
        if lm["kind"] == "assistant" and lm["stop"] in ("end_turn", "stop_sequence", "max_tokens", "refusal"):
            live_bg = [t for t in self.bg.values() if now - t["ts"] < BG_WINDOW]
            if live_bg:  # 턴은 끝났지만 백그라운드 명령 결과를 기다리는 중 = 사이드바에선 진행 중
                return "working", f"백그라운드 작업 {len(live_bg)}개 기다림", age
            return "idle", "턴 끝남", age
        if age < 60:
            if lm["kind"] == "assistant" and tool:
                return "working", "도구 " + tool, age
            if lm["kind"] == "tool_result":
                return "working", "도구 결과 처리 중", age
            return "working", "응답 중", age
        if lm["kind"] == "assistant" and lm["stop"] == "tool_use" and age < 6 * 3600:
            return "waiting_tool", f"{tool or '도구'} 뒤 {int(age // 60)}분째 응답 없음 — 승인 대기이거나 긴 작업", age
        if lm["kind"] in ("prompt", "tool_result") and age < 600:
            return "working", "응답 기다리는 중", age
        return "idle", "멈춤", age


class Tracker(threading.Thread):
    """작업 폴더의 대화 파일을 interval 초마다 훑어 새로 붙은 줄만 읽는다."""

    def __init__(self, workspace, days=7, interval=1.5):
        super().__init__(daemon=True)
        self.workspace = workspace
        self.days = days
        self.interval = interval
        self.files = {}
        self.lock = threading.Lock()
        self.version = 0
        self.ready = False

    def run(self):
        while True:
            try:
                self.scan()
                self.ready = True
            except Exception as e:  # 한 번 실패해도 다음 주기에 다시
                print("tracker:", e, flush=True)
            time.sleep(self.interval)

    def scan(self):
        cutoff = time.time() - self.days * 86400
        changed = False
        for d in project_dirs(self.workspace):
            for f in d.glob("*.jsonl"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                key = str(f)
                fs = self.files.get(key)
                if fs is None:
                    if st.st_mtime < cutoff:
                        continue
                    fs = FileState(f)
                    with self.lock:
                        self.files[key] = fs
                if st.st_size < fs.offset:  # 잘렸으면 처음부터
                    with self.lock:
                        self.files[key] = fs = FileState(f)
                if st.st_size > fs.offset:
                    with open(f, "rb") as h:
                        h.seek(fs.offset)
                        raw = h.read(st.st_size - fs.offset)
                    with self.lock:
                        fs.offset += len(raw)
                        fs.feed(raw)
                    changed = True
        if changed:
            self.version += 1

    def edges(self, mapping, names, now):
        """최근 48시간 세션 간 연결. 쌍(보낸 쪽, 받는 쪽, 종류)마다 마지막 것만, 받는 쪽 기록으로 상태를 정한다.
        unread(받은 뒤 기록 없음) · working(받은 뒤 돌고 있음) · handled(받은 뒤 턴이 끝났지만 회신 없음) · replied(되보냄) · unknown(받는 쪽 파일 모름)"""
        with self.lock:
            items = list(self.files.values())
        by_sid = {}
        for fs in items:
            sid = mapping.get(fs.cli)
            if sid and (sid not in by_sid or (fs.last_ts or 0) > (by_sid[sid].last_ts or 0)):
                by_sid[sid] = fs
        latest = {}
        for fs in items:
            a = mapping.get(fs.cli)
            if not a:
                continue
            for ev in fs.events:
                if not ev["ts"] or now - ev["ts"] > EDGE_WINDOW:
                    continue
                to = ev["to"]
                b = to if to.startswith("local_") else names.get(to, "")
                if not b or b == a:
                    continue
                key = (a, b, ev["kind"])
                if key not in latest or ev["ts"] > latest[key]["ts"]:
                    latest[key] = {"from": a, "to": b, "kind": ev["kind"], "ts": ev["ts"], "summary": ev["summary"]}
        out = []
        for (a, b, kind), e in latest.items():
            back = latest.get((b, a, "msg"))
            fb = by_sid.get(b)
            if back and back["ts"] > e["ts"]:
                st = "replied"
            elif not fb:
                st = "unknown"
            elif fb.last_end_ts and fb.last_end_ts > e["ts"] and fb.state(now)[0] in ("idle", "asking"):
                st = "handled"
            elif (fb.last_ts or 0) > e["ts"] and fb.state(now)[0] in ("working", "waiting_tool"):
                st = "working"
            elif (fb.last_ts or 0) > e["ts"]:
                st = "handled"
            else:
                st = "unread"
            e = dict(e, state=st, age=int(now - e["ts"]), at=datetime.fromtimestamp(e["ts"], KST).strftime("%m-%d %H:%M"))
            del e["ts"]
            out.append(e)
        out.sort(key=lambda x: x["age"])
        return out

    def timeline(self, mapping, names, hours=6):
        """세션 타임라인: 세션마다 활동 구간(막대)과 창 안의 모든 메시지·띄우기 이벤트(세로 화살표)."""
        now = time.time()
        t0 = now - hours * 3600
        with self.lock:
            items = list(self.files.values())
        latest = {(e["from"], e["to"], e["kind"]): e["state"] for e in self.edges(mapping, names, now)}
        lanes, events = {}, []
        for fs in items:
            sid = mapping.get(fs.cli)
            key = sid or "cli:" + fs.cli
            segs = [[max(a, t0), b] for a, b in fs.segs if b >= t0]
            if segs:
                lane = lanes.setdefault(key, {"sid": sid or "", "cli": fs.cli, "segs": [], "last": 0})
                lane["segs"] += segs
                lane["last"] = max(lane["last"], fs.last_ts or 0)
            if not sid:
                continue
            for ev in fs.events:
                if not ev["ts"] or ev["ts"] < t0:
                    continue
                to = ev["to"]
                b = to if to.startswith("local_") else names.get(to, "")
                if not b or b == sid:
                    continue
                events.append({"from": sid, "to": b, "kind": ev["kind"], "ts": ev["ts"], "summary": ev["summary"],
                               "at": datetime.fromtimestamp(ev["ts"], KST).strftime("%m-%d %H:%M")})
        # 쌍마다 가장 최근 이벤트에만 지금 상태, 나머지는 past
        newest = {}
        for e in events:
            k = (e["from"], e["to"], e["kind"])
            if k not in newest or e["ts"] > newest[k]["ts"]:
                newest[k] = e
        for e in events:
            k = (e["from"], e["to"], e["kind"])
            e["state"] = latest.get(k, "past") if newest[k] is e else "past"
        out = sorted(lanes.values(), key=lambda x: -x["last"])
        return {"t0": t0, "t1": now, "hours": hours, "lanes": out, "events": sorted(events, key=lambda e: e["ts"])}

    def heatmap(self, days=7):
        """요일×시간 히트맵(KST): 칸마다 출력 토큰·전체 토큰·활동 분."""
        today = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=days - 1)
        t0 = start.timestamp()
        cells = [[{"out": 0, "tot": 0, "min": 0.0} for _ in range(24)] for _ in range(days)]
        with self.lock:
            items = list(self.files.values())
        for fs in items:
            for t, u in list(fs.usage.values()):
                if t < t0:
                    continue
                dt = datetime.fromtimestamp(t, KST)
                di = (dt.date() - start.date()).days
                if 0 <= di < days:
                    c = cells[di][dt.hour]
                    c["out"] += u["output_tokens"]
                    c["tot"] += sum(u.values())
            for a, b in list(fs.segs):
                if b < t0:
                    continue
                a = max(a, t0)
                while a < b:  # 구간을 시간 칸 경계로 잘라 분을 더한다
                    dt = datetime.fromtimestamp(a, KST)
                    edge = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                    piece = min(b, edge.timestamp()) - a
                    di = (dt.date() - start.date()).days
                    if 0 <= di < days:
                        cells[di][dt.hour]["min"] += piece / 60
                    a += piece if piece > 0 else 1
        labels = [(start + timedelta(days=i)).strftime("%m-%d %a") for i in range(days)]
        for row in cells:
            for c in row:
                c["min"] = round(c["min"])
        return {"days": labels, "cells": cells}

    def snapshot(self, mapping, names=None, exclude=()):
        """mapping: cli id → 사이드바 세션 id. 짝이 없는 최근 세션은 unmapped 로.
        exclude: 보관·삭제한 세션의 cli id(board/board-archived.json). 토큰 합계엔 넣되 카드로는 안 낸다(11-10 보관한 세션이 임시 카드로 되살아난 일)."""
        now = time.time()
        today = datetime.now(KST).date().isoformat()
        sessions, unmapped = {}, []
        tot_today = dict.fromkeys(TOKEN_KEYS, 0)
        running = 0
        with self.lock:
            items = list(self.files.values())
        for fs in items:
            total, day = fs.sums(today)
            for k in TOKEN_KEYS:
                tot_today[k] += day[k]
            state, label, age = fs.state(now)
            if state in ("working", "waiting_tool") and fs.cli not in exclude:  # 묻고 멈춘 세션은 도는 세션이 아니다
                running += 1
            q = fs.question() if state == "asking" else None
            if fs.cli in exclude:
                continue
            e = {
                "cli": fs.cli, "title": fs.title, "state": state, "label": label, "age": int(age), "ts": int(fs.last_ts or 0), "uts": int(fs.user_ts or 0),
                "last": datetime.fromtimestamp(fs.last_ts, KST).strftime("%m-%d %H:%M") if fs.last_ts else "",
                # sums() 의 캐시 dict 를 그대로 넘기면 아래 합치기에서 캐시가 오염되므로 복사
                "text": fs.last_text, "model": fs.model, "tokens": dict(total), "today": dict(day),
                "ask": {"text": q["text"], "options": q["options"], "app": q.get("app") or [], "kind": q["kind"]} if q else None,
            }
            e["bg"], e["bg_done"] = fs.background(now)
            sid = mapping.get(fs.cli)
            if sid:
                prev = sessions.get(sid)
                if prev:  # 한 세션에 파일이 여럿이면 토큰은 더하고 상태는 최근 것
                    for k in TOKEN_KEYS:
                        e["tokens"][k] = e["tokens"][k] + prev["tokens"][k]
                        e["today"][k] = e["today"][k] + prev["today"][k]
                    if prev["age"] < e["age"]:
                        e.update({x: prev[x] for x in ("state", "label", "age", "last", "text", "model", "cli", "ask", "ts", "uts", "bg", "bg_done")})
                sessions[sid] = e
            elif state in ("working", "waiting_tool") or age < 86400:  # 판정 뒤 생긴 세션: 돌고 있거나 하루 안에 움직인 것만 임시 카드로(오래된 질문은 빼기)
                unmapped.append(e)
        return {
            "now": datetime.now(KST).strftime("%H:%M:%S"), "ready": self.ready,
            "sessions": sessions, "unmapped": unmapped, "edges": self.edges(mapping, names or {}, now),
            "totals": {"today": tot_today, "running": running, "files": len(items)},
        }
