"""board_host.py — 상황판이 Claude Code CLI 세션을 직접 돌린다(데스크톱 앱 없이 답하기 · 승인 · 선택지 답).

  claude -p --input-format stream-json --output-format stream-json --verbose --permission-prompt-tool stdio
         [--resume <대화 id> | --session-id <새 id>] [--model …] [--effort …] [--permission-mode …]

- 작업자가 상황판 입력창에 친 글이 이 프로세스의 stdin 으로 바로 들어간다 → 그 세션에는 작업자 본인 입력이다
  (관제 세션을 거친 「다른 세션의 메시지」가 아니라서 승인도 받아들여진다).
- 도구 실행 허용 · 선택지 질문(AskUserQuestion)은 CLI 가 control_request(can_use_tool)로 묻고, 상황판 버튼이
  control_response(allow + updatedInput / deny + message)로 답한다(11-11 시험: 여러 턴 · 허용 · 거부 · 선택지 · --resume 모두 통과).
- 대화 파일은 앱 세션과 같은 ~/.claude/projects/<폴더>/<대화 id>.jsonl 에 쌓이므로 상세 창 「대화」가 그대로 보여 준다.
- 같은 대화를 데스크톱 앱이 열고 있으면(앱의 claude.exe 가 --resume=<대화 id> 로 떠 있음) 띄우지 않는다 — 두 곳에서
  따로 이어 쓰면 대화가 갈라진다.
표준 라이브러리 + ctypes 만 쓴다."""
import ctypes
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
IDLE_CLOSE = 30 * 60  # 이만큼 조용하면 프로세스를 닫는다(다음에 보내면 --resume 으로 다시)
MODES = {"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto", "manual"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def find_cli():
    """Claude Code CLI(claude). PATH 에 없으면 npm 기본 위치."""
    for n in ("claude.cmd", "claude.exe", "claude"):
        p = shutil.which(n)
        if p:
            return p
    p = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"
    return str(p) if p.exists() else None


# ── 데스크톱 앱이 이 대화를 열고 있나(Windows: 프로세스 명령줄의 --resume=<id>) ──
# Windows 구조체는 Windows 에서만 만든다(macOS 상주가 서버를 품을 때 이 파일을 불러도 깨지지 않게)
if os.name == "nt":
    import ctypes.wintypes as W

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", W.USHORT), ("MaximumLength", W.USHORT), ("Buffer", ctypes.c_void_p)]

    class _PE32(ctypes.Structure):
        _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", W.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _cmdlines(exe_name="claude.exe"):
    """이름이 exe_name 인 프로세스들의 명령줄(같은 사용자 것만 읽힌다). PowerShell 을 띄우지 않고 ctypes 로."""
    return [c for c, _ in _procs(exe_name)]


def _procs(exe_name="claude.exe"):
    """이름이 exe_name 인 프로세스들의 (명령줄, 시작 시각 epoch). 시작 시각을 못 읽으면 0."""
    if os.name != "nt":
        return []
    k32, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
    k32.CreateToolhelp32Snapshot.restype = W.HANDLE
    k32.OpenProcess.restype = W.HANDLE
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    pe = _PE32()
    pe.dwSize = ctypes.sizeof(_PE32)
    pids = []
    ok = k32.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        if pe.szExeFile.lower() == exe_name:
            pids.append(pe.th32ProcessID)
        ok = k32.Process32NextW(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    out = []
    for pid in pids:
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            need = W.ULONG(0)
            ntdll.NtQueryInformationProcess(h, 60, None, 0, ctypes.byref(need))  # ProcessCommandLineInformation
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            if ntdll.NtQueryInformationProcess(h, 60, buf, need.value, ctypes.byref(need)) != 0:
                continue
            us = _UNICODE_STRING.from_buffer(buf)
            cmd = ctypes.wstring_at(us.Buffer, us.Length // 2)
            created = 0.0
            ft = [W.FILETIME() for _ in range(4)]  # 시작 · 끝 · 커널 · 사용자 시간
            if k32.GetProcessTimes(h, *[ctypes.byref(x) for x in ft]):
                t100 = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime  # 1601-01-01 부터 100ns 단위
                created = (t100 - 116444736000000000) / 1e7
            out.append((cmd, created))
        except Exception:
            pass
        finally:
            k32.CloseHandle(h)
    return out


_CMDS = {"at": 0.0, "val": []}
_CMDS_LOCK = threading.Lock()


def _procs_cached(ttl=3.0):
    """프로세스 목록은 몇 초 묶어 쓴다(상세 창이 2.5초마다 묻고, 프로세스가 수십 개라 한 번에 0.1초쯤 걸림)."""
    with _CMDS_LOCK:
        if time.time() - _CMDS["at"] > ttl:
            _CMDS["val"] = _procs()
            _CMDS["at"] = time.time()
        return _CMDS["val"]


def _cmdlines_cached(ttl=3.0):
    return [c for c, _ in _procs_cached(ttl)]


_CLI_ID = re.compile(r"--(?:resume|session-id)[= ]\"?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def cli_procs():
    """대화 id → 그 대화를 잡고 있는 claude.exe(앱 · 상황판이 띄운 것)의 시작 시각 epoch. 같은 id 가 여럿이면 가장 이른 것.
    Windows 밖이면 None(모름) — 실시간 층은 그때 시간 창으로만 백그라운드 작업을 판단한다(11-19)."""
    if os.name != "nt":
        return None
    out = {}
    for cmd, created in _procs_cached(5.0):
        m = _CLI_ID.search(cmd)
        if m:
            cid = m.group(1)
            out[cid] = min(out.get(cid) or created, created) if created else out.get(cid, 0.0)
    return out


def held_by_app(cli_id, fresh=False):
    """데스크톱 앱(Claude-Data 아래 claude.exe)이 --resume=<cli_id> 로 이 대화를 잡고 있으면 True.
    상황판이 띄운 CLI(npm 설치 경로에도 claude-code 가 들어감)를 앱으로 오인하지 않게 Claude-Data 경로로 가른다."""
    try:
        for c in (_cmdlines() if fresh else _cmdlines_cached()):
            if "/claude-data/" in c.replace("\\", "/").lower() and re.search(r"--resume[= ]\"?" + re.escape(cli_id), c):
                return True
    except Exception:
        pass
    return False


class Hosted:
    """상황판이 돌리는 CLI 세션 하나."""

    def __init__(self, cli_id, cwd, resume, model=None, effort=None, mode=None):
        self.cli_id, self.cwd, self.resume = cli_id, cwd, resume
        self.model, self.effort, self.mode = model, effort, (mode if mode in MODES else "default")
        self.proc = None
        self.pending = {}  # request_id → {kind, tool, input, ts}
        self.busy = False
        self.last = time.time()
        self.error = ""
        self.reqn = 0
        self.wlock = threading.Lock()

    def start(self):
        cli = find_cli()
        if not cli:
            raise RuntimeError("Claude Code CLI(claude)를 찾지 못했어요 — 설치(npm i -g @anthropic-ai/claude-code) 후 터미널에서 claude 로 한 번 로그인해 주세요")
        args = [cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--permission-prompt-tool", "stdio", "--permission-mode", self.mode]
        if self.mode == "bypassPermissions":
            args.append("--allow-dangerously-skip-permissions")
        args += ["--resume", self.cli_id] if self.resume else ["--session-id", self.cli_id]
        if self.model:
            args += ["--model", self.model]
        if self.effort:
            args += ["--effort", self.effort]
        self.proc = subprocess.Popen(args, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        threading.Thread(target=self._read, daemon=True, name=f"host-{self.cli_id[:8]}").start()
        threading.Thread(target=self._err, daemon=True).start()
        self._control({"subtype": "initialize", "hooks": None})
        self.resume = True  # 다음에 다시 띄우면 이어받기

    def alive(self):
        return bool(self.proc) and self.proc.poll() is None

    def _write(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self.wlock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def _control(self, request):
        self.reqn += 1
        self._write({"type": "control_request", "request_id": f"so_{self.reqn}", "request": request})

    def send(self, text):
        self.error = ""
        self.busy = True
        self.last = time.time()
        self._write({"type": "user", "message": {"role": "user", "content": text}, "parent_tool_use_id": None, "session_id": self.cli_id})

    def answer(self, request_id, behavior, message="", answers=None):
        p = self.pending.pop(request_id, None)
        if not p:
            raise KeyError("이미 답했거나 없는 요청")
        if behavior == "allow":
            inp = dict(p["input"])
            if p["kind"] == "question" and answers:
                inp["answers"] = answers
            resp = {"behavior": "allow", "updatedInput": inp}
        else:
            resp = {"behavior": "deny", "message": message or "작업자가 상황판에서 거부함"}
        self.last = time.time()
        self._write({"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": resp}})

    def interrupt(self):
        self._control({"subtype": "interrupt"})

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def _err(self):
        for raw in self.proc.stderr:
            t = raw.decode("utf-8", "replace").strip()
            if t:
                self.error = t[:400]

    def _read(self):
        for raw in self.proc.stdout:
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            typ = d.get("type")
            self.last = time.time()
            if typ == "control_request":
                req = d.get("request") or {}
                if req.get("subtype") == "can_use_tool":
                    tool = str(req.get("tool_name") or "")
                    self.pending[d.get("request_id")] = {"kind": "question" if tool == "AskUserQuestion" else "tool", "tool": tool,
                                                         "input": req.get("input") or {}, "ts": time.time()}
                else:  # 그 밖의 요청(훅 등)은 그냥 성공으로
                    self._write({"type": "control_response", "response": {"subtype": "success", "request_id": d.get("request_id"), "response": {}}})
            elif typ == "result":
                self.busy = False
                if d.get("is_error") or str(d.get("subtype", "")).startswith("error"):
                    self.error = str(d.get("result") or d.get("subtype"))[:400]
        self.busy = False
        self.pending.clear()

    def state(self):
        def view(rid, p):
            inp = p["input"]
            v = {"request_id": rid, "kind": p["kind"], "tool": p["tool"]}
            if p["kind"] == "question":
                v["questions"] = inp.get("questions") or []
            else:
                v["summary"] = json.dumps(inp, ensure_ascii=False)[:1500]
            return v
        return {"alive": self.alive(), "busy": self.busy and self.alive(), "cli": self.cli_id, "mode": self.mode,
                "model": self.model or "", "error": self.error, "idle": int(time.time() - self.last),
                "pending": [view(r, p) for r, p in list(self.pending.items())]}


class HostManager:
    """상황판이 돌리는 세션들. 조용한 지 IDLE_CLOSE 넘은 것은 닫는다(묻는 중이면 그대로)."""

    def __init__(self):
        self.sessions = {}
        self.released = {}  # 대화 id → 시각: 작업자가 Claude 앱에서 같은 세션을 열어 상황판이 놓은 것
        self.lock = threading.Lock()
        threading.Thread(target=self._reap, daemon=True, name="host-reaper").start()

    def get(self, cli_id):
        return self.sessions.get(cli_id)

    def ensure(self, cli_id, cwd, resume, model=None, effort=None, mode=None):
        if not UUID_RE.match(cli_id):
            raise ValueError("bad cli id")
        with self.lock:
            h = self.sessions.get(cli_id)
            if h and h.alive():
                return h
            if resume and held_by_app(cli_id, fresh=True):  # 넘겨받기 직전엔 묶어 둔 값 말고 지금 값으로
                raise PermissionError("Claude 앱이 이 세션을 열고 있어요. 앱에서 이 세션을 보관하거나 앱을 끈 뒤 여기서 이어 가세요(두 곳에서 따로 쓰면 대화가 갈라져요)")
            h = Hosted(cli_id, cwd, resume, model, effort, mode)
            h.start()
            self.sessions[cli_id] = h
            self.released.pop(cli_id, None)
            return h

    def new_id(self):
        return str(uuid.uuid4())

    def list(self):
        return [h.state() for h in self.sessions.values() if h.alive()]

    def _reap(self):
        while True:
            time.sleep(10)
            for cid, h in list(self.sessions.items()):
                if not h.alive():
                    self.sessions.pop(cid, None)
                elif not h.busy and not h.pending and held_by_app(cid):
                    # 작업자가 Claude 앱 사이드바에서 같은 세션을 열었다 → 쉬는 중이면 바로 놓는다(두 곳에서 이어 쓰면 대화가 갈라진다)
                    h.close()
                    self.sessions.pop(cid, None)
                    self.released[cid] = time.time()
                elif not h.busy and not h.pending and time.time() - h.last > IDLE_CLOSE:
                    h.close()
                    self.sessions.pop(cid, None)
