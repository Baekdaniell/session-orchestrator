"""board-render.py — 세션 판정 JSON 을 로컬 상황판 HTML 로 만든다.

사용: python -I scripts/board-render.py board/board-sessions.json board/session-board.html
- 템플릿: scripts/board-template.html 의 __BOARD_DATA__ 자리에 JSON 을 넣는다.
- 브라우저가 file:// 에서 JSON 을 따로 못 읽으므로 데이터를 HTML 안에 넣는다.
- /board 스킬이 갱신 때마다 실행한다. 결과 파일은 Edge 에서 열어 두고 새로고침한다.
- 결과 파일 옆에 board-snippet.css(사용자 CSS 스니펫)가 없으면 안내 주석만 든 빈 파일을 만들어 준다. 있으면 절대 건드리지 않는다.
"""
import json
import secrets
import sys
from pathlib import Path

REQUIRED = ("session", "status", "section", "todo", "due", "result", "last_active", "handed_by", "judged_at")
STATUSES = {"내 답 대기", "실행 중", "완료", "정리 후보", "관제"}  # 관제 = 이 상황판을 만드는 세션 자신(한 행).

# 옵시디언 CSS 스니펫과 같은 역할. 템플릿의 <link id="snippet" href="board-snippet.css"> 가 읽는다.
SNIPPET = """\
/* board-snippet.css — 상황판 사용자 CSS (옵시디언 CSS 스니펫과 같은 역할)
   - /board 가 상황판을 다시 만들어도 이 파일은 그대로 둔다. 없을 때만 이 안내문으로 새로 만든다.
   - 켜고 끄기: 상황판 오른쪽 아래 ⚙ → CSS 스니펫.
   - 선택자: <html> 에 data-layout="레이아웃 id" · data-skin="스킨 id" · data-theme="테마 id" · data-mode="light|dark" · data-font="system|serif|mono" 가 붙는다.
   - 기본값: 레이아웃 kanban + 스킨 primary + 테마 primary
   - 레이아웃 id: kanban gallery note list rail
   - 스킨 id: flat primary brutalist material glass neumorph sketch terminal neon editorial (질감만, 색은 안 바꿈)
   - 테마 id: notion obsidian minimal things nord catppuccin dracula solarized gruvbox rosepine everforest primary velvet mono terminal neon
   - 테마 한 벌 = 변수 9개: --bg(배경) --panel(카드·표) --fg(글자) --accent(강조)
                            --c-blue --c-purple --c-green --c-red --c-yellow(태그 색: 분석·툴 개발·검수 실행·결함 산출·새 섹션)
   - 파생 변수(필요하면 덮어쓰기): --line --hover --callout --fg-2 --fg-3 --danger --danger-bg --shadow --font --fs --radius

   예1) 노션 테마 라이트의 강조색만 바꾸기
   [data-theme="notion"][data-mode="light"] { --accent: #2f80ed; }

   예2) 다크일 때 카드 모서리를 각지게
   [data-mode="dark"] .card { border-radius: 4px; }

   예3) 커버 숨기기
   .cover { display: none; }
*/
"""


def main(src: str, dst: str) -> None:
    rows = json.loads(Path(src).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        sys.exit("JSON 최상위는 배열이어야 합니다")
    titles = set()
    for i, r in enumerate(rows):
        missing = [k for k in REQUIRED if k not in r]
        if missing:
            sys.exit(f"{i}번 행에 필드 누락: {missing}")
        if r["status"] not in STATUSES:
            sys.exit(f"{i}번 행 status 값 이상: {r['status']!r}")
        if r["session"] in titles:
            sys.exit(f"세션 제목 중복: {r['session']!r}")
        titles.add(r["session"])
        if r.get("summary") is not None and not (isinstance(r["summary"], list) and all(isinstance(x, str) for x in r["summary"])):
            sys.exit(f"{i}번 행 summary 는 문자열 배열이어야 합니다")
        if r.get("branch") is not None and not isinstance(r["branch"], str):
            sys.exit(f"{i}번 행 branch 는 문자열이어야 합니다")
        link = r.get("link", "")
        if link and not link.startswith("claude://claude.ai/epitaxy/local_"):
            sys.exit(f"{i}번 행 link 형식 이상: {link!r}")

    template = (Path(__file__).with_name("board-template.html")).read_text(encoding="utf-8")
    out_dir = Path(dst).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    # 로컬 서버(board-server.py) API 토큰: 없으면 만들고, 있으면 그대로 쓴다(페이지와 서버가 같은 값을 본다).
    token_file = out_dir / ".board-token"
    if not token_file.exists():
        token_file.write_text(secrets.token_hex(16), encoding="utf-8")
    token = token_file.read_text(encoding="utf-8").strip()
    for d in ("inbox", "working", "done"):  # 요청 큐 폴더
        (out_dir / d).mkdir(exist_ok=True)

    html = template
    for placeholder, value in (("__BOARD_DATA__", json.dumps(rows, ensure_ascii=False).replace("<", "\\u003c")), ("__BOARD_TOKEN__", token)):
        n = html.count(placeholder)
        if n != 1:
            sys.exit(f"템플릿에 {placeholder} 가 {n}번 있습니다(정확히 1번이어야 함)")
        # </script> 로 스크립트 블록이 끊기지 않게 데이터의 < 는 위에서 < 로 이스케이프했다.
        html = html.replace(placeholder, value)
    if "board-data" not in html:
        sys.exit("데이터 삽입 실패")
    Path(dst).write_text(html, encoding="utf-8", newline="\n")
    snippet = Path(dst).with_name("board-snippet.css")
    if not snippet.exists():
        snippet.write_text(SNIPPET, encoding="utf-8", newline="\n")
        print(f"{snippet}: 사용자 CSS 스니펫 파일을 새로 만들었습니다(비어 있음)")
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("내 답 대기", "실행 중", "완료", "정리 후보")}
    print(f"{dst}: {len(rows)}행, " + " · ".join(f"{k} {v}" for k, v in counts.items()))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
