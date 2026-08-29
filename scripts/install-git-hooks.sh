#!/bin/sh
# 初回のみ実行: Push 時のバージョン自動更新フックを有効化
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
chmod +x .githooks/pre-push
git config core.hooksPath .githooks
echo "Git hooks installed. Push 時に App バージョンが自動更新されます。"
