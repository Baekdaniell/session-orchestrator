"""board-map.py — 사이드바 세션(local_…)과 대화 파일(cli id)을 짝지어 board-sessions.json 에 cli_id 로 적는다.

사용: python -I scripts/board-map.py board/sessions-meta.json board/board-sessions.json
- sessions-meta.json: 관제 세션이 list_sessions 결과에서 뽑아 쓴 [{"sid": "local_…", "lastActivityAt": "ISO", "cwd": "…"}].
  관제 세션 자신은 list_sessions 에 안 나오므로 {"sid": …, "cli": "<알고 있는 cli id>"} 로 직접 넣는다(그 파일은 후보에서 늘 제외).
  "probe": "<그 세션 마지막 문장 일부>" 를 주면 시각으로 못 찾을 때 파일 안에서 그 문장을 찾아 짝짓는다
  (긴 도구를 돌리는 중이거나 /model 같은 명령이 마지막이면 시각과 맞는 줄이 없다 — 11-10 실측).
- 방법: 세션의 lastActivityAt 과 ±3초 안에 찍힌 줄이 있는 대화 파일을 찾는다(그 세션의 cwd 폴더 우선).
  후보가 여럿이면 마지막 줄 시각이 lastActivityAt 에 가장 가까운 파일. 못 찾거나 애매하면 적지 않고 출력만 한다.
- 이미 cli_id 가 있는 행은 다시 찾지 않는다(--all 이면 전부 다시).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I 로 실행해도 같은 폴더의 board_live 를 쓰게
import board_live  # noqa: E402

WORKSPACE = board_live.watched_workspace(Path(os.environ.get("SO_HOME") or Path(__file__).resolve().parent.parent) / "board")  # exe 는 SO_HOME
TOL = 3.0


def main(meta_path: str, rows_path: str, redo: bool) -> None:
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    rows = json.loads(Path(rows_path).read_text(encoding="utf-8"))
    by_sid = {r["link"].rsplit("/", 1)[-1]: r for r in rows if r.get("link")}

    files = [f for d in board_live.project_dirs(WORKSPACE) for f in d.glob("*.jsonl")]
    index = {}  # 파일 → 정렬된 timestamp 목록 (필요할 때만 읽음)

    def ts_of(f):
        if f not in index:
            index[f] = sorted(board_live.timestamps(f))
        return index[f]

    taken = {r["cli_id"] for r in rows if r.get("cli_id")}
    control = {m["cli"] for m in meta if m.get("cli")}
    done, miss = 0, []
    for m in meta:
        sid = m["sid"]
        row = by_sid.get(sid)
        if not row or (row.get("cli_id") and not redo):
            continue
        if m.get("cli"):
            row["cli_id"] = m["cli"]
            done += 1
            continue
        t = board_live.parse_ts(m.get("lastActivityAt", ""))
        want = board_live.slug(m.get("cwd", ""))
        cands = []
        for f in files if t else []:
            try:
                if f.stat().st_mtime < t - TOL:  # 그 시각 뒤로 쓴 적 없는 파일은 후보 아님
                    continue
            except OSError:
                continue
            ts = ts_of(f)
            if any(abs(x - t) <= TOL for x in ts):
                cands.append((f.parent.name != want, abs(ts[-1] - t), f))
        # 관제 세션 파일은 모든 세션을 인용하므로 언제나 후보에서 뺀다
        cands = [c for c in cands if c[2].stem not in control and (c[2].stem not in taken or redo)]
        if not cands and m.get("probe"):
            # 보조: 관제 세션이 판정 때 읽은 그 세션의 문장 일부를 파일에서 찾는다(대화 파일은 한글을 UTF-8 그대로 저장)
            probe = m["probe"].encode("utf-8")
            hits = []
            for f in files:
                if f.stem in control or (f.stem in taken and not redo):
                    continue
                try:
                    if probe in f.read_bytes():
                        hits.append(f)
                except OSError:
                    continue
            if len(hits) == 1:
                cands = [(False, 0, hits[0])]
            elif len(hits) > 1:
                miss.append((row["session"], "문장으로도 후보 둘 이상: " + ", ".join(h.stem[:8] for h in hits[:3])))
                continue
        if not cands:
            miss.append((row["session"], "±3초 안에 맞는 파일 없음" + ("" if m.get("probe") else " (probe 를 주면 문장으로 다시 찾음)")))
            continue
        cands.sort()
        if len(cands) > 1 and cands[0][:2] == cands[1][:2]:
            miss.append((row["session"], "후보 둘 이상 동률: " + ", ".join(c[2].stem[:8] for c in cands[:3])))
            continue
        row["cli_id"] = cands[0][2].stem
        taken.add(row["cli_id"])
        done += 1

    Path(rows_path).write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
    have = sum(1 for r in rows if r.get("cli_id"))
    print(f"새로 짝지음 {done} · 짝 있음 {have}/{len(rows)} · 못 찾음 {len(miss)}")
    for name, why in miss:
        print(f"  - {name}: {why}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = [a for a in sys.argv[1:] if a != "--all"]
    if len(args) != 2:
        sys.exit(__doc__)
    main(args[0], args[1], "--all" in sys.argv)
