# Phát triển Rust

## Lệnh kiểm tra chất lượng

Giữ tối đa hai job để không làm chậm máy:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -j 2 -- -D warnings
cargo test --workspace -j 2
cargo build --release -p anoti-app -j 2
```

Kiểm tra target Windows từ Linux (chỉ biên dịch thử, không chạy file `.exe`):

```bash
cargo check --workspace --all-targets --target x86_64-pc-windows-gnu -j 2
```

## Vòng đời cài đặt trong sandbox

```bash
AI_AGENT_NOTIFIER_PROFILE_ROOT=/duong/dan/profile-tam target/debug/anoti install
AI_AGENT_NOTIFIER_PROFILE_ROOT=/duong/dan/profile-tam target/debug/anoti update
AI_AGENT_NOTIFIER_PROFILE_ROOT=/duong/dan/profile-tam target/debug/anoti uninstall
```
