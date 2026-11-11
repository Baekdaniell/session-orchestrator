"""board-queue.py — 상황판 요청 큐(board/inbox → working → done) 를 관제 세션이 다루는 도구.

상황판 페이지의 「갱신」 버튼과 카드 채팅은 board-server.py 를 거쳐 board/inbox/<id>.json 으로 떨어진다.
관제 세션은 이 스크립트로 기다리고(wait), 꺼내고(take), 끝냈다고 적는다(done).

사용 (리포 루트에서):
  python -I scripts/board-queue.py wait            inbox 에 요청이 생길 때까지 블록(1초 폴링). 생기면 id 를 한 줄씩 찍고 종료.
                                                   기다리는 동안 board/.watcher-armed 를 만들어 두고 종료 때 지운다(페이지의 "연결됨" 배지).
                                                   → 관제 세션은 이 명령을 Bash run_in_background 로 띄워 두고, 끝나면 깨어나 take 한다.
  python -I scripts/board-queue.py take            inbox/*.json 전부를 working/ 으로 옮기고 내용을 JSON 배열로 출력.
  python -I scripts/board-queue.py done <id> [--error] [--result 텍스트]
                                                   working/<id>.json 을 done/ 으로 옮기며 state·result·finished_at 기록.
  python -I scripts/board-queue.py list            폴더별 개수.

요청 JSON: {"id", "type": "refresh"|"message", "at", (message 일 때) "session_id", "session", "text"}
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("SO_HOME") or Path(__file__).resolve().parent.parent) / "board"  # exe 는 SO_HOME 으로 데이터 폴더를 넘긴다


def _files(d: str):
    return sorted((ROOT / d).glob("*.json"))


def cmd_wait(_a) -> None:
    for d in ("inbox", "working", "done"):
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    flag = ROOT / ".watcher-armed"
    flag.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    try:
        while True:
            found = _files("inbox")
            if found:
                for p in found:
                    print(p.stem, flush=True)
                return
            time.sleep(1)
    finally:
        flag.unlink(missing_ok=True)


def cmd_take(_a) -> None:
    (ROOT / "working").mkdir(parents=True, exist_ok=True)
    out = []
    for p in _files("inbox"):
        dst = ROOT / "working" / p.name
        p.replace(dst)
        try:
            out.append(json.loads(dst.read_text(encoding="utf-8")))
        except Exception as e:  # 깨진 파일은 바로 error 로 끝낸다
            dst.replace(ROOT / "done" / p.name)
            out.append({"id": p.stem, "type": "broken", "error": str(e)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_done(a) -> None:
    src = ROOT / "working" / (a.id + ".json")
    if not src.exists():
        sys.exit(f"working/{a.id}.json 이 없습니다 (take 를 먼저 했는지 확인)")
    d = json.loads(src.read_text(encoding="utf-8"))
    d.update({"state": "error" if a.error else "done", "result": a.result or "", "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    (ROOT / "done").mkdir(parents=True, exist_ok=True)
    dst = ROOT / "done" / src.name
    dst.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    src.unlink()
    print(f"{a.id}: {d['state']}")


def cmd_list(_a) -> None:
    for d in ("inbox", "working", "done"):
        print(f"{d}: {len(_files(d))}")
    print("armed:", (ROOT / ".watcher-armed").exists())


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("wait").set_defaults(fn=cmd_wait)
    sub.add_parser("take").set_defaults(fn=cmd_take)
    p = sub.add_parser("done")
    p.add_argument("id")
    p.add_argument("--error", action="store_true")
    p.add_argument("--result", default="")
    p.set_defaults(fn=cmd_done)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
