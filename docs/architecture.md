# Kiến trúc AI agent desktop notifier

## Phạm vi

Runtime production hiện tại được viết bằng Rust. GJS chỉ tồn tại trong GNOME Shell extension vì mã này phải chạy bên trong compositor. Shell và PowerShell chỉ là bootstrap cài đặt; không chứa logic thông báo.

## Thành phần

```text
AI agent
  → anoti hook <agent>
    → capture session đồng bộ hoặc notify tách rời
      → session store / queue / dedupe
        → Linux backend hoặc Windows backend
          → popup, focus, active probe, âm thanh và native notification
```

- `anoti-core` sở hữu schema, khóa tệp, ghi JSON nguyên tử, session cache, queue, dedupe và bộ chọn identity.
- `anoti-hooks` chuẩn hóa payload Claude Code, Codex và Antigravity thành `HookAction`.
- `anoti-app` thực thi hook, nạp identity theo `session_id`, điều phối một popup tại một thời điểm và quản lý install/update/uninstall.
- `anoti-platform-linux` dùng X11 trực tiếp hoặc hợp đồng D-Bus của GNOME Shell trên Wayland.
- `anoti-platform-windows` dùng Win32 cho discovery, focus, monitor, DPI, overlay và toast.
- `anoti-delivery` gửi webhook có timeout.

## Bất biến identity

Một record chỉ có `precision="window"` khi có đủ token cửa sổ native, PID cửa sổ lớn hơn 1 và fingerprint tiêu đề. Marker ứng dụng như `wayland:gnome-terminal` không phải identity cửa sổ và không bao giờ được lưu như exact.

Hook đầu phiên chạy capture đồng bộ. Khi thông báo đến, `anoti` tra `session_id` và sao chép toàn bộ identity exact vào queue item. Nút focus và auto-dismiss chỉ đọc snapshot này. Session exact là write-once để hook lặp không thể gắn phiên sang cửa sổ khác.

## GNOME Wayland

Extension hỗ trợ GNOME Shell 42–50 và cung cấp `CaptureActiveWindowV3`, `CaptureWindowByTitleV5`, `FocusWindowV4` cùng `IsWindowActiveV4`. App identity được chuẩn hóa dấu chấm, gạch ngang và ký tự phân cách, nên `org.gnome.Terminal` khớp lớp `gnome-terminal`.

Capture trả token `wayland:<stable-sequence>`, PID, tiêu đề và app id. Khi nhiều cửa sổ GNOME Terminal dùng chung PID máy chủ, runtime dùng `caller_tty` và `GNOME_TERMINAL_SCREEN` để đặt một title marker tạm thời trên đúng TTY, lấy native token qua `CaptureWindowByTitleV5`, rồi khôi phục title stack. Nếu phiên GNOME Shell vẫn đang nạp contract v3/v4 cũ, title marker được dùng làm evidence duy nhất để kích hoạt đúng cửa sổ trước khi gọi `CaptureActiveWindowV3`; đường tương thích này có hiệu lực mà không cần đăng xuất. Focus/active kiểm tra token trước, sau đó PID và evidence bổ sung. Sau yêu cầu activation, runtime chỉ báo focus thành công khi compositor xác nhận đúng cửa sổ đã active; popup được ẩn trước activation để việc đóng popup không giành lại focus. Token cũ hoặc không khớp trả `false`; hệ thống không đoán một cửa sổ khác. AT-SPI chỉ được dùng để dò active khi adapter không khả dụng, không dùng để focus.

## Hàng đợi và đồng thời

Queue item có trạng thái `queued` hoặc `displaying`. Khóa overlay liên tiến trình đảm bảo chỉ một UI được hiển thị. Mỗi tiến trình thông báo chờ lease, lấy item queued cũ nhất rồi hiển thị. `anoti focus` ưu tiên item đang hiển thị, sau đó mới đến item queued. Item chỉ bị xóa sau dismiss hoặc focus đã được xác minh.

## Vòng đời cài đặt

`artifacts/manifest.json` là nguồn sự thật cho artifact Linux và Windows. `anoti install/update` sao chép chính binary hiện tại, nhúng extension, merge hook theo ownership marker, lưu rollback và health-check binary. Quá trình này dọn các engine/hook Python legacy, thay các entry Codex còn trỏ tới `.codex/notify.py` và cập nhật namespace Antigravity `desktop-notifier` sang lệnh Rust, nhưng không xóa hook bên thứ ba. Antigravity được ghi ở cả `~/.gemini/settings.json` và namespace `desktop-notifier` trong `~/.gemini/config/hooks.json` để tương thích hai loader đang được hỗ trợ. `anoti uninstall` gỡ đúng phần dự án sở hữu và khôi phục cấu hình Codex trước đó nếu có.

Thay đổi kiến trúc phải cập nhật implementation cho nền tảng liên quan, kiểm thử Rust, manifest/script vòng đời khi cần, tài liệu này và README nếu hành vi công khai thay đổi.

## Kiểm thử

Kiểm thử dùng chung chạy trên host thật; kiểm thử nền tảng khác dùng abstraction/mock. Không báo đã chạy Windows native khi chỉ cross-check từ Linux. Các test cần desktop thật được đánh dấu ignored và nêu rõ điều kiện chạy.
