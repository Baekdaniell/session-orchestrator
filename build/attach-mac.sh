#!/bin/bash
# 이미 있는 GitHub 릴리스에 macOS dmg 를 올리고(같은 이름이면 바꿔 끼움), 본문에 macOS 설치 단락이 없으면 끝에 덧붙인다.
# 릴리스를 만들지는 않는다 — 릴리스 등록은 손으로 한다(Windows exe 를 시험한 뒤). build-mac.yml 이 쓴다.
#
#   bash build/attach-mac.sh <태그 vX.Y.Z> <dmg 경로>        (gh 로그인 또는 GH_TOKEN, 저장소는 GH_REPO 또는 현재 폴더)
#
# 같은 태그에 두 번 돌아도 된다: 파일은 바꿔 끼우고, 본문은 <!-- macos-install --> 표시가 있으면 손대지 않는다.
set -euo pipefail

TAG="${1:?태그(vX.Y.Z)를 주세요}"
FILE="${2:?dmg 경로를 주세요}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

gh release upload "$TAG" "$FILE" --clobber
echo "릴리스 $TAG 에 올렸습니다: $(basename "$FILE")"
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT
gh release view "$TAG" --json body --jq .body > "$BODY"
if ! grep -qU 'macos-install' "$BODY"; then   # -U: Windows 의 grep 에서 돌려 볼 때도 같게(Linux 에선 효과 없음)
  { echo; bash "$ROOT/build/release-notes.sh" --mac-section "$TAG"; } >> "$BODY"
  gh release edit "$TAG" --notes-file "$BODY"
  echo "본문에 macOS 설치 단락을 덧붙였습니다"
fi
