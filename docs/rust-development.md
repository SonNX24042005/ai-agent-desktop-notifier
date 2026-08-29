# Phát triển Rust

## Lệnh chất lượng

Giữ tối đa hai job để không làm chậm máy:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -j 2 -- -D warnings
cargo test --workspace -j 2
cargo build --release -p anoti-app -j 2
```

Kiểm tra target Windows từ Linux, không chạy file `.exe`:

```bash
cargo check --workspace --target x86_64-pc-windows-gnu -j 2
```

## Lifecycle trong sandbox

```bash
AI_AGENT_NOTIFIER_PROFILE_ROOT=/duong/dan/profile-tam target/debug/anoti install
AI_AGENT_NOTIFIER_PROFILE_ROOT=/duong/dan/profile-tam target/debug/anoti update
AI_AGENT_NOTIFIER_PROFILE_ROOT=/duong/dan/profile-tam target/debug/anoti uninstall
```

## Test desktop thật

Hai test Linux tương tác bị bỏ qua mặc định:

```bash
cargo test -p anoti-platform-linux native_accessibility_bus_returns_a_bounded_non_match -j 2 -- --ignored
GDK_BACKEND=x11 xvfb-run -a cargo test -p anoti-platform-linux native_background_click_dismisses_overlay -j 2 -- --ignored
```

Trên GNOME Wayland, kiểm tra version extension đã nạp bằng `gnome-extensions info ai-agent-desktop-notifier@sonnx24042005`. Sau khi thay extension, phiên Shell Wayland có thể cần đăng xuất và đăng nhập lại.
