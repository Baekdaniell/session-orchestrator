"""board_docs.py — 상황판 「문서」: 감시 폴더 안의 md 파일을 읽기 전용으로 보여 준다.

세션들이 남기는 결과물(분석 노트 · 티켓 초안 · 인계 문서 · 메모리)은 대부분 md 파일이라, 상황판에서 바로 읽게 한다.
- read: 경로 하나를 읽는다. 상대 경로는 (보던 문서의 폴더 → 그 세션의 작업 폴더 → 감시 폴더) 순으로 찾는다.
- touched: 세션 대화 파일 끝부분에서 그 세션이 쓰고 · 고치고 · 읽고 · 말한 md 파일을 모은다(상세 창 「문서」).
- recent: 감시 폴더와 메모리 폴더에서 최근에 바뀐 md 파일(도구줄 「📄 문서」 목록).

안전: 내주는 건 확장자 .md · .markdown 파일뿐이고, 실제 경로(링크를 따라간 뒤)가 감시 폴더나
그 폴더의 대화 폴더(~/.claude/projects/<슬러그>*, 메모리가 있는 곳) 안일 때만 읽는다. 쓰기는 없다.
서버는 토큰을 확인한 뒤에만 이 함수들을 부른다.
"""

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote

import board_live

MD_EXT = (".md", ".markdown")
MAX_BYTES = 2_000_000          # 이보다 크면 앞부분만 보여 준다
TOUCH_WINDOW = 12_000_000      # 대화 파일 끝에서 이만큼만 훑는다(수백 MB 파일도 있어서)
SKIP_DIRS = {"node_modules", "venv", "__pycache__", "site-packages", "board", "dist", "build", "AppData", "Library",
             "Application Data", "OneDrive"}
OPS = {"Write": "작성", "Edit": "수정", "MultiEdit": "수정", "NotebookEdit": "수정", "Read": "읽음"}
# 글 속에 나온 md 경로: [글](경로.md) 의 경로, `경로.md`, 공백으로 끊기는 경로.md
MENTION_RE = re.compile(r"(?:\]\(|`|^|[\s(\"'])((?:[A-Za-z]:)?[^\s`()\"'<>|*?\[\]]*?[^\s`()\"'<>|*?\[\]/\\]\.(?:md|markdown))(?=[\s`)\"':#,.]|$)",
                        re.IGNORECASE | re.MULTILINE)


def roots(workspace) -> list:
    """읽어도 되는 폴더: 감시 폴더 + 그 폴더(와 워크트리)의 대화 폴더."""
    out = []
    for p in [Path(workspace)] + board_live.project_dirs(workspace):
        try:
            out.append(p.resolve())
        except OSError:
            pass
    return out


def inside(p: Path, allowed) -> bool:
    return any(p == r or r in p.parents for r in allowed)


def clean(s: str) -> str:
    """링크 · 코드에서 온 경로 다듬기: 따옴표 · <> · file:/// · #앵커 · :줄번호 를 뗀다."""
    s = str(s or "").strip().strip("<>\"'")
    if s.lower().startswith("file:///"):
        s = unquote(s[8:])
    s = re.sub(r"#[^/\\]*$", "", s)
    s = re.sub(r"(\.(?:md|markdown))(?::\d+){1,2}$", r"\1", s, flags=re.IGNORECASE)
    return s.strip()


def resolve(path, allowed, bases=()) -> Path | None:
    """경로 → 실제 md 파일(허용 폴더 안). 못 찾거나 밖이면 None."""
    s = clean(path)
    if not s or "\0" in s:
        return None
    p = Path(os.path.expanduser(s))
    cands = [p] if p.is_absolute() else [Path(b) / p for b in bases if b]
    for c in cands:
        try:
            r = c.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if r.suffix.lower() in MD_EXT and r.is_file() and inside(r, allowed):
            return r
    return None


def resolve_wiki(name, allowed, bases=()) -> Path | None:
    """[[이름]] → 같은 폴더의 이름.md (메모리 파일은 이름의 - 를 _ 로 바꾼 파일명이 많다)."""
    name = str(name or "").split("|", 1)[0].strip()
    if not name or re.search(r"[\\/:\0]", name):
        return None
    for b in bases:
        for stem in dict.fromkeys((name, name.replace("-", "_"), name.replace("_", "-"))):
            hit = resolve(stem + ".md", allowed, [b])
            if hit:
                return hit
    return None


def label(p: Path, workspace) -> str:
    """보여 줄 짧은 경로: 감시 폴더 안이면 상대 경로, 대화 폴더 안이면 「메모리/…」 식, 아니면 전체 경로."""
    ws = Path(workspace).resolve()
    try:
        return p.relative_to(ws).as_posix()
    except ValueError:
        pass
    for d in board_live.project_dirs(workspace):
        try:
            rel = p.relative_to(d.resolve()).as_posix()
            return rel.replace("memory/", "메모리/", 1) if rel.startswith("memory/") else rel
        except (ValueError, OSError):
            continue
    return str(p)


def read(p: Path, workspace) -> dict:
    st = p.stat()
    with open(p, "rb") as f:
        raw = f.read(MAX_BYTES + 1)
    truncated = len(raw) > MAX_BYTES
    text = raw[:MAX_BYTES].decode("utf-8-sig", "replace")
    if truncated:  # 잘린 마지막 줄은 버린다
        text = text[: text.rfind("\n") + 1]
    return {"path": str(p), "dir": str(p.parent), "rel": label(p, workspace), "name": p.name, "size": st.st_size,
            "mtime": int(st.st_mtime), "ver": version(p, st), "text": text.replace("\r\n", "\n"), "truncated": truncated}


def version(p: Path, st=None) -> str:
    """바뀌었는지 비교할 값(수정 시각 ns + 크기). 열어 둔 문서를 몇 초마다 다시 볼 때 같으면 본문을 안 보낸다."""
    st = st or p.stat()
    return f"{st.st_mtime_ns}-{st.st_size}"


def transcript_cwd(path) -> str:
    """대화 파일의 작업 폴더(마지막으로 적힌 cwd). 상대 경로를 그 세션 기준으로 풀 때 쓴다."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 262_144))
            tail = f.read()
        hits = re.findall(rb'"cwd":"((?:[^"\\]|\\.)*)"', tail)
        if not hits:
            with open(path, "rb") as f:
                hits = re.findall(rb'"cwd":"((?:[^"\\]|\\.)*)"', f.read(262_144))
        return json.loads(b'"' + hits[-1] + b'"') if hits else ""
    except (OSError, ValueError):
        return ""


def touched(transcript, workspace, limit=60) -> list:
    """세션이 다룬 md 파일: 도구(작성 · 수정 · 읽음)와 Claude 글에 나온 경로(언급). 최근 것부터, 지금 있는 파일만."""
    allowed = roots(workspace)
    cwd = transcript_cwd(transcript)
    bases = [b for b in (cwd, str(workspace)) if b]
    size = os.path.getsize(transcript)
    with open(transcript, "rb") as f:
        f.seek(max(0, size - TOUCH_WINDOW))
        raw = f.read()
    if size > TOUCH_WINDOW:
        raw = raw[raw.find(b"\n") + 1:]
    seen = {}  # 실제 경로 → {ops, ts}

    def add(path, op, ts, base_list):
        p = resolve(path, allowed, base_list)
        if not p:
            return
        e = seen.setdefault(str(p), {"p": p, "ops": [], "ts": ""})
        if op not in e["ops"]:
            e["ops"].append(op)
        if ts and ts > e["ts"]:
            e["ts"] = ts

    for line in raw.splitlines():
        if b'"assistant"' not in line or (b".md" not in line and b".MD" not in line and b".markdown" not in line):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        ts = d.get("timestamp", "")
        line_cwd = d.get("cwd") or cwd
        lb = [b for b in (line_cwd, str(workspace)) if b]
        for x in (d.get("message") or {}).get("content") or []:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "tool_use":
                inp = x.get("input") if isinstance(x.get("input"), dict) else {}
                op = OPS.get(str(x.get("name", "")))
                fp = inp.get("file_path") or inp.get("notebook_path") or ""
                if op and isinstance(fp, str) and fp.lower().endswith(MD_EXT):
                    add(fp, op, ts, lb)
            elif x.get("type") == "text":
                for m in MENTION_RE.finditer(x.get("text", "")):
                    add(m.group(1), "언급", ts, lb)
    out = sorted(seen.values(), key=lambda e: e["ts"], reverse=True)[:limit]
    res = []
    for e in out:
        try:
            mt = int(e["p"].stat().st_mtime)
        except OSError:
            continue
        res.append({"path": str(e["p"]), "rel": label(e["p"], workspace), "name": e["p"].name, "ops": e["ops"],
                    "ts": e["ts"], "mtime": mt})
    return res


_recent = {"key": None, "at": 0.0, "val": None}


def recent(workspace, limit=80, budget_s=1.5, max_entries=40_000, depth=6) -> dict:
    """감시 폴더(숨은 폴더 · 의존성 폴더 제외)와 메모리 폴더의 md 파일을 최근 바뀐 순으로. 30초 캐시.
    감시 폴더가 홈 같은 큰 폴더여도 멈추지 않게 시간 · 항목 수에 상한을 둔다(넘으면 partial)."""
    key = str(workspace)
    now = time.time()
    if _recent["key"] == key and now - _recent["at"] < 30:
        return _recent["val"]
    found, partial, count, t0 = [], False, 0, time.monotonic()
    stack = [(Path(workspace), 0)]
    while stack:
        d, lv = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                count += 1
                if count > max_entries or time.monotonic() - t0 > budget_s:
                    partial = True
                    stack.clear()
                    break
                try:
                    if e.is_dir(follow_symlinks=False):
                        if lv < depth and not e.name.startswith(".") and e.name not in SKIP_DIRS:
                            stack.append((Path(e.path), lv + 1))
                    elif e.name.lower().endswith(MD_EXT):
                        found.append((e.stat().st_mtime, Path(e.path)))
                except OSError:
                    continue
    for pd in board_live.project_dirs(workspace):
        for f in (pd / "memory").glob("*.md"):
            try:
                found.append((f.stat().st_mtime, f))
            except OSError:
                pass
    found.sort(key=lambda x: x[0], reverse=True)
    items = [{"path": str(p), "rel": label(p, workspace), "name": p.name, "mtime": int(mt),
              "memory": p.parent.name == "memory" and p.parent.parent.parent == board_live.PROJECTS}
             for mt, p in found[:limit]]
    val = {"items": items, "partial": partial, "scanned": count}
    _recent.update(key=key, at=now, val=val)
    return val
