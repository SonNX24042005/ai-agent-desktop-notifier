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

Một record chỉ có `precision="window"` khi có đủ token cửa sổ native, PID cửa sổ lớn hơn 1 và fingerprint tiêu đề. Mỗi lần tồn tại của cửa sổ trong phiên capture được gán một `window_instance_id` (UUID v4 ngẫu nhiên không lặp lại) theo vòng đời đã capture. Marker ứng dụng như `wayland:gnome-terminal` không phải identity cửa sổ và không bao giờ được lưu như exact.

Hook đầu phiên chạy capture đồng bộ. Khi thông báo đến, `anoti` ưu tiên tra `session_id`. Khi một capture lặp lại từ cùng nguồn (cùng caller process, cùng process start time), runtime giữ ổn định `generation` và `window_instance_id`. Chỉ khi có evidence nguồn thay đổi được chứng minh (`capture_source_changed`: caller PID mới, process start time mới, TTY mới), phiên mới được rebind, `generation` tăng lên và một `window_instance_id` UUID mới được tạo. Khi đó, các thông báo mang `generation` cũ hoặc UUID cũ bị từ chối fail-closed trong `resolve_exact` để tránh focus nhầm cửa sổ cũ.

Để chống tái sử dụng PID sau khi tiến trình kết thúc và khởi động lại, runtime ghi nhận thời điểm khởi động của tiến trình (`process_start_time` qua `/proc/<pid>/stat` trên Linux và `GetProcessTimes` trên Windows); nếu start time thay đổi, ứng viên bị đánh dấu stale và từ chối.

Đối với trường hợp tái sử dụng handle trong cùng tiến trình (same-PID + same-handle + same-process-start + same-caller reuse): một external notifier không chạy trong không gian địa chỉ của ứng dụng không thể phát hiện hoặc chứng minh việc handle bị đóng và mở lại nội bộ nếu không có sự kiện capture hoặc rebind mới từ agent. Khi có capture/rebind mới với nguồn thay đổi, `generation` tăng và UUID mới làm toàn bộ notification cũ fail-closed. Khi chưa có rebind, runtime bảo đảm exact instance match (khớp handle, PID, process start time) vẫn resolve và focus thành công ngay cả khi tiêu đề cửa sổ đã thay đổi hoàn toàn (ví dụ khi terminal chuyển lệnh shell hoặc ứng dụng con). Tiêu đề chỉ được dùng như evidence/fallback phân định khi thiếu exact instance, không bao giờ dùng để vô hiệu hóa một exact instance hợp lệ.

Nếu việc giải quyết mục tiêu gặp nhiều ứng viên đồng hạng ở mức bằng chứng cao nhất mà không thể phân định, runtime trả về `FocusOutcome::Ambiguous` thay vì chọn ngẫu nhiên một cửa sổ.

Trước khi kích hoạt cửa sổ đích, runtime khôi phục trạng thái nếu cửa sổ đang bị thu nhỏ (`SW_RESTORE` trên Win32 hoặc unminimize trên X11) và xác nhận cửa sổ thực sự chuyển sang foreground; nếu kích hoạt thất bại hoặc hết thời gian chờ, runtime trả về lỗi an toàn mà không kích hoạt cửa sổ tùy ý. Identity exact được giữ ổn định trong vòng đời một tiến trình agent; một `SessionStart` lặp từ cùng caller không thể gắn phiên sang cửa sổ khác. Khi cùng session được mở lại bởi caller mới hoặc tiến trình mới, một capture exact mới được phép thay token cũ để xử lý việc đổi cửa sổ, khởi động lại ứng dụng hoặc đăng nhập lại GNOME. Capture pending không bao giờ được hạ cấp một identity exact. Queue chưa có token được phép nâng cấp bằng record exact đã xác minh ngay trước khi hiển thị hoặc focus.

## GNOME Wayland

Extension hỗ trợ GNOME Shell 42–50 và cung cấp `CaptureActiveWindowV3`, `CaptureWindowByTitleV5`, `FocusWindowV4`, `IsWindowActiveV4` cùng `KeepOverlayAboveV6`. App identity được chuẩn hóa dấu chấm, gạch ngang và ký tự phân cách, nên `org.gnome.Terminal` khớp lớp `gnome-terminal`.

Capture trả token `wayland:<stable-sequence>`, PID, tiêu đề và app id. Khi nhiều cửa sổ GNOME Terminal dùng chung PID máy chủ, runtime dùng `caller_tty` và `GNOME_TERMINAL_SCREEN` để đặt một title marker tạm thời trên đúng TTY, lấy native token qua `CaptureWindowByTitleV5`, rồi khôi phục title stack. Nếu phiên GNOME Shell vẫn đang nạp contract v3/v4 cũ, title marker được dùng làm evidence duy nhất để kích hoạt đúng cửa sổ trước khi gọi `CaptureActiveWindowV3`; đường tương thích này có hiệu lực mà không cần đăng xuất. Focus/active kiểm tra token trước, sau đó PID và evidence bổ sung. Sau yêu cầu activation, runtime chỉ báo focus thành công khi compositor xác nhận đúng cửa sổ đã active; popup được ẩn trước activation để việc đóng popup không giành lại focus. Token cũ hoặc không khớp trả `false`; hệ thống không đoán một cửa sổ khác. AT-SPI chỉ được dùng để dò active khi adapter không khả dụng, không dùng để focus.

Trên GNOME Wayland, GTK tạo popup dưới dạng notification top-level không nhận keyboard focus. Sau khi map cửa sổ, runtime gửi PID tiến trình qua `KeepOverlayAboveV6`; extension chỉ chấp nhận cửa sổ cùng PID và đúng tiêu đề nội bộ, sau đó gọi `make_above()` và `stick()` để popup không bị cửa sổ ứng dụng che và tiếp tục hiện khi đổi workspace. X11/XWayland vẫn dùng `keep_above` và type hint của window manager. Nếu contract extension cũ chưa được nạp, runtime bỏ qua bước promote an toàn và các chức năng focus hiện có vẫn hoạt động.

## Hàng đợi và đồng thời

Queue item có trạng thái `queued` hoặc `displaying`. Khóa overlay liên tiến trình đảm bảo chỉ một UI được hiển thị. Mỗi tiến trình thông báo chờ lease, lấy item queued cũ nhất rồi hiển thị. `anoti focus` ưu tiên item đang hiển thị, sau đó mới đến item queued. Item chỉ bị xóa sau dismiss hoặc focus đã được xác minh.

## Vòng đời cài đặt

`artifacts/manifest.json` là nguồn sự thật cho artifact Linux và Windows. `anoti install/update` sao chép chính binary hiện tại, nhúng extension, merge hook theo ownership marker, lưu rollback và health-check binary. Quá trình này dọn các engine/hook Python legacy, thay các entry Codex còn trỏ tới `.codex/notify.py` và cập nhật namespace Antigravity `desktop-notifier` sang lệnh Rust, nhưng không xóa hook bên thứ ba. Antigravity được ghi ở cả `~/.gemini/settings.json` và namespace `desktop-notifier` trong `~/.gemini/config/hooks.json` để tương thích hai loader đang được hỗ trợ. `anoti uninstall` gỡ đúng phần dự án sở hữu và khôi phục cấu hình Codex trước đó nếu có.

Thay đổi kiến trúc phải cập nhật implementation cho nền tảng liên quan, kiểm thử Rust, manifest/script vòng đời khi cần, tài liệu này và README nếu hành vi công khai thay đổi.

## Kiểm thử

Kiểm thử dùng chung chạy trên host thật; kiểm thử nền tảng khác dùng abstraction/mock. Không báo đã chạy Windows native khi chỉ cross-check từ Linux. Các test cần desktop thật được đánh dấu ignored và nêu rõ điều kiện chạy.
