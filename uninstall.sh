#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
INSTALLED_BINARY="${HOME:?HOME chưa được thiết lập}/.local/bin/anoti"
SOURCE_BINARY="$SCRIPT_DIR/target/release/anoti"

if [ -x "$INSTALLED_BINARY" ]; then
    "$INSTALLED_BINARY" uninstall
elif [ -x "$SOURCE_BINARY" ]; then
    "$SOURCE_BINARY" uninstall
else
    echo "Không tìm thấy runtime Rust đã cài hoặc bản build cục bộ." >&2
    exit 1
fi

echo "Đã gỡ runtime và các hook do anoti quản lý."
