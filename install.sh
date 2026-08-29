#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_URL="https://github.com/SonNX24042005/ai-agent-desktop-notifier.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
TEMP_DIR=""

cleanup() {
    if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
        rm -rf -- "$TEMP_DIR"
    fi
}
trap cleanup EXIT

if [ ! -f "$SCRIPT_DIR/Cargo.toml" ]; then
    command -v git >/dev/null 2>&1 || { echo "Thiếu git." >&2; exit 1; }
    TEMP_DIR="$(mktemp -d)"
    git clone --depth 1 "$REPOSITORY_URL" "$TEMP_DIR/repository"
    SCRIPT_DIR="$TEMP_DIR/repository"
fi

command -v cargo >/dev/null 2>&1 || {
    echo "Thiếu Rust toolchain. Hãy cài rustup/cargo rồi chạy lại." >&2
    exit 1
}

echo "Đang biên dịch runtime Rust..."
(cd "$SCRIPT_DIR" && CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}" cargo build --release -p anoti-app)
"$SCRIPT_DIR/target/release/anoti" install

echo "Đã cài runtime Rust. Trên Wayland, hãy đăng xuất rồi đăng nhập lại nếu GNOME Shell chưa nạp extension mới."
