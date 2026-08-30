# Kiến trúc AI agent desktop notifier

## Phạm vi

Runtime production được xây dựng hoàn toàn bằng Rust, cung cấp giải pháp phát thông báo native đa nền tảng (Linux và Windows) cho các agent Claude Code, OpenAI Codex và Google Antigravity. Dự án tập trung phát thông báo native chuẩn của hệ điều hành và gửi webhook, không can thiệp sâu vào việc dò tìm hoặc chuyển focus cửa sổ ứng dụng.

## Luồng hoạt động

```text
AI agent (Claude / Codex / Antigravity)
  → anoti hook <agent>
    → chuẩn hóa payload sự kiện sang NotificationRequest
      → anoti notify
        → kiểm tra chống lặp (DedupeStore)
        → gửi webhook bất đồng bộ (nếu có cấu hình)
        → phát âm thanh cảnh báo (nếu có)
        → gọi dịch vụ thông báo native của hệ điều hành (Linux FreeDesktop D-Bus / Windows Toast)
```

## Các thành phần chính

- `anoti-core`: Định nghĩa model `NotificationRequest`, `Urgency`, `EventKind`, `PlatformCapabilities`, đường dẫn runtime và cơ chế chống lặp nguyên tử `DedupeStore`.
- `anoti-hooks`: Chuẩn hóa các sự kiện đầu vào từ Claude Code, OpenAI Codex và Google Antigravity thành các hành động thông báo `HookAction::Notify`. Xử lý an toàn các payload không hợp lệ (fail-open) và quản lý việc hợp nhất cấu hình hook mà không làm ảnh hưởng đến cấu hình của bên thứ ba.
- `anoti-platform`: Khai báo trait trừu tượng `PlatformBackend` với hai phương thức chính: `native_notify` và `play_sound`.
- `anoti-platform-linux`: Triển khai `PlatformBackend` trên Linux thông qua giao thức D-Bus chuẩn `org.freedesktop.Notifications`, tương thích với GNOME, KDE, XFCE và các desktop environment phổ biến khác mà không yêu cầu extension riêng.
- `anoti-platform-windows`: Triển khai `PlatformBackend` trên Windows sử dụng Windows Toast notification (WinRT/Win32) chuẩn và âm thanh hệ thống.
- `anoti-delivery`: Quản lý cấu hình webhook và gửi dữ liệu thông báo bất đồng bộ tới các endpoint như Slack, Discord, Feishu, DingTalk hoặc webhook tùy chỉnh.
- `anoti-app`: Entry point binary `anoti`, tiếp nhận CLI arguments, điều phối các bước phát thông báo và quản lý vòng đời cài đặt (`install`, `update`, `uninstall`).

## Chống lặp và phát âm thanh

- Mỗi thông báo mới được băm SHA-256 theo tổ hợp `app_name|title|message` và ghi nhận trong `DedupeStore` với khóa tệp liên tiến trình.
- Các thông báo trùng lặp trong khoảng thời gian 2 giây sẽ được bỏ qua nhằm tránh spam khi agent kích hoạt nhiều hook liên tiếp.
- Âm thanh được phát thông qua các trình phát media có sẵn trên Linux (`paplay`, `pw-play`, `canberra-gtk-play`, `aplay`) hoặc `PlaySoundW` trên Windows.

## Biểu tượng thông báo và danh tính ứng dụng

- Duy trì một danh tính ứng dụng thông báo `anoti` duy nhất trên cả Linux FreeDesktop và Windows (`APP_USER_MODEL_ID = "io.github.sonnx24042005.AiAgentNotifier"`).
- Mỗi sự kiện thông báo được gắn biểu tượng trực quan theo từng agent (Claude Code, OpenAI Codex, Google Antigravity) hoặc biểu tượng mặc định của `anoti` làm fallback khi agent không xác định.
- Trên Linux: chuyển đường dẫn biểu tượng hợp lệ qua D-Bus `app_icon` và hint `image-path`.
- Trên Windows: chèn thẻ `appLogoOverride` với đường dẫn biểu tượng tương ứng vào XML mẫu `ToastGeneric`.
- Toàn bộ asset biểu tượng vector SVG và raster PNG được nhúng trong binary, cài đặt đối xứng vào `.local/share/anoti/icons/` và tự động dọn dẹp khi gỡ cài đặt.

## Vòng đời cài đặt và dọn dẹp

- `artifacts/manifest.json` đóng vai trò là nguồn thông tin chính xác về các artifact cần quản lý trên Linux và Windows.
- Khi thực hiện `anoti install` hoặc `anoti update`:
  - Binary thực thi được cài đặt vào thư mục `.local/bin` của người dùng.
  - Các tệp biểu tượng PNG và SVG được cài đặt vào `.local/share/anoti/icons` và thư mục hicolor icons.
  - Các cấu hình hook cho Claude Code (`.claude/settings.json`), Codex (`.codex/config.toml`, `.codex/hooks.json`), và Antigravity (`.gemini/settings.json`, `.gemini/config/hooks.json`) được cập nhật theo danh sách marker sở hữu.
  - Tự động phát hiện, vô hiệu hóa và dọn dẹp thư mục extension GNOME Shell cũ (`~/.local/share/gnome-shell/extensions/ai-agent-desktop-notifier@sonnx24042005`) cũng như các tệp trạng thái runtime cũ nếu tồn tại từ các phiên bản trước.
  - Tệp rollback được sao lưu để khôi phục an toàn nếu kiểm tra sức khỏe của binary mới thất bại.
- Khi thực hiện `anoti uninstall`:
  - Gỡ bỏ toàn bộ cấu hình hook do `anoti` quản lý, khôi phục trạng thái cấu hình của người dùng.
  - Xóa binary, các tệp biểu tượng và các artifact liên quan.

## Kiểm thử đa nền tảng

- Mã nguồn và kiểm thử Rust là nguồn sự thật chính của dự án.
- Môi trường Linux thực hiện kiểm thử trực tiếp trên hệ thống; các kiểm thử dành cho Windows được xác minh thông qua unit test, mock API và kiểm tra biên dịch chéo (`cargo check --target x86_64-pc-windows-gnu`).
