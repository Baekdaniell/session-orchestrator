#!/bin/bash
# Session Orchestrator 제거 (macOS). --keep-data 를 붙이면 판정 데이터(board 폴더)는 남긴다.
exec bash "$(cd "$(dirname "$0")" && pwd)/install.sh" --uninstall "$@"
