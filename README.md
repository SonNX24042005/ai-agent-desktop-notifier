# AI agent desktop notifier (anoti)

`anoti` là runtime Rust dùng chung cho thông báo desktop của Claude Code, OpenAI Codex và Google Antigravity trên Linux và Windows. Runtime chuyển trực tiếp các sự kiện cần chú ý từ agent sang hệ thống thông báo native của hệ điều hành và hỗ trợ webhook từ xa.

## Điểm chính

- Một binary Rust duy nhất cho CLI, hook adapter, chống lặp, webhook và quản lý cài đặt.
- Backend Linux gọi trực tiếp dịch vụ thông báo desktop FreeDesktop/GNOME (`org.freedesktop.Notifications`) qua D-Bus, không cần GNOME Shell extension.
- Backend Windows sử dụng toast notification chuẩn của Windows (WinRT/Win32).
- Tự động chống lặp thông báo trong khoảng thời gian ngắn (2 giây) và phát âm thanh tương ứng theo sự kiện.
- Hỗ trợ gửi webhook đồng thời tới các dịch vụ như Slack, Discord, Feishu, DingTalk hoặc endpoint tùy chỉnh.
- Xử lý bất đồng bộ, không chặn luồng làm việc của agent khi phát sinh thông báo.
- Bộ cài đặt và cập nhật tự động dọn dẹp các extension GNOME cũ và tệp runtime trạng thái cũ nếu có, đồng thời bảo toàn nguyên vẹn cấu hình hook của bên thứ ba.

## Yêu cầu

- Rust 1.85 trở lên và Cargo để biên dịch từ nguồn.
- Linux: Desktop environment hỗ trợ FreeDesktop notification service (GNOME, KDE, XFCE, v.v.).
- Windows 10 hoặc Windows 11.

## Cài đặt

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.ps1 | iex
```

Bộ cài đặt chỉ biên dịch Rust với tối đa hai job mặc định, sau đó dùng `anoti install` để cài binary vào đường dẫn người dùng và cấu hình hook mà vẫn giữ nguyên thiết lập của bên thứ ba.

## Lệnh thường dùng

```bash
anoti doctor
anoti status
anoti test
anoti config
anoti update
anoti uninstall
anoti --title "Xong việc" --message "Tiến trình đã hoàn tất"
```

## Tích hợp agent

- Claude Code: sự kiện câu hỏi, yêu cầu cấp quyền và hoàn thành câu trả lời.
- Codex: sự kiện `notify`, yêu cầu cấp quyền và hoàn thành lượt làm việc (`agent-turn-complete`).
- Antigravity: sự kiện câu hỏi, yêu cầu cấp quyền và hoàn thành phản hồi.

Các hook chạy tách rời để không làm gián đoạn tương tác của agent. Payload không hợp lệ được xử lý an toàn (fail-open).

## Cấu trúc mã nguồn

```text
crates/anoti-core              model dữ liệu, deduplication và đường dẫn runtime
crates/anoti-hooks             adapter payload và cấu hình hook agent
crates/anoti-platform          hợp đồng backend trừu tượng
crates/anoti-platform-linux    backend D-Bus notification native trên Linux
crates/anoti-platform-windows  backend Windows toast native
crates/anoti-delivery          quản lý và gửi webhook
crates/anoti-app               CLI, điều phối thông báo và lifecycle
artifacts/manifest.json        danh sách artifact được quản lý
```

Xem thêm [kiến trúc](docs/architecture.md), [nguồn biểu tượng](docs/icons.md), [hướng dẫn phát triển Rust](docs/rust-development.md) và [hướng dẫn Windows](docs/windows-guide.md).

## Giấy phép

[MIT](LICENSE)
