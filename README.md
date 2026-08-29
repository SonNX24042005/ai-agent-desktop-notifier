# AI agent desktop notifier (anoti)

`anoti` là runtime Rust dùng chung cho thông báo desktop của Claude Code, OpenAI Codex và Google Antigravity trên Linux và Windows. Runtime ghi nhớ chính xác cửa sổ phát sinh thông báo, hiển thị popup, tự đóng khi cửa sổ đích được focus và cung cấp lệnh chuyển tới thông báo đang chờ.

## Điểm chính

- Một binary Rust cho CLI, hook adapter, hàng đợi, chống lặp, webhook và quản lý cài đặt.
- Backend Linux tách X11 và GNOME Wayland; backend Windows dùng Win32.
- Trên GNOME Wayland, extension chụp token `wayland:<stable-sequence>`, PID và tiêu đề của cửa sổ đang active lúc bắt đầu phiên.
- Với nhiều cửa sổ GNOME Terminal dùng chung PID, TTY riêng của từng phiên được dùng để chụp đúng native window token, kể cả khi Codex `SessionStart` chạy trễ.
- Mỗi thông báo đóng băng identity của phiên (`window_instance_id` bằng UUID ngẫu nhiên cho từng lifetime). Cửa sổ hợp lệ vẫn focus chính xác kể cả khi tiêu đề terminal thay đổi hoàn toàn; không chọn gần đúng khi nhiều cửa sổ cùng ứng dụng gây kết quả mơ hồ.
- Khi một phiên được rebind với bằng chứng nguồn thay đổi được chứng minh (caller mới, tiến trình mới), `SessionStart` chụp lại token native, tăng generation và cấp UUID mới để loại bỏ fail-closed các thông báo cũ; cache cũ chỉ được thay khi caller mới đã được xác minh.
- Nếu agent dùng ID khác nhau giữa sự kiện bắt đầu và hoàn thành, runtime liên kết bằng caller đã capture và chỉ dùng kết quả khi xác định duy nhất một cửa sổ.
- Nút chuyển cửa sổ chỉ đóng popup sau khi hệ điều hành xác nhận đúng cửa sổ đích đã active; nếu chuyển thất bại, popup hiện lại để thử tiếp.
- Popup GNOME Wayland được extension giữ trên lớp cửa sổ ứng dụng và trên mọi workspace cho đến khi đóng, focus thành công hoặc hết thời gian chờ.
- Chỉ một popup được hiển thị tại một thời điểm; các yêu cầu khác chờ trong hàng đợi liên tiến trình.
- Runtime Python cũ đã được loại khỏi production path. Cài đặt hoặc cập nhật sẽ dọn cả tệp hook Python và các entry Codex/Antigravity cũ còn trỏ tới chúng trong hồ sơ người dùng, đồng thời giữ nguyên hook bên thứ ba.

## Yêu cầu

- Rust 1.85 trở lên và Cargo để biên dịch từ nguồn.
- Linux: GTK3, X11/XRandR và các thư viện phát triển tương ứng.
- GNOME Wayland: GNOME Shell extension đi kèm repository.
- Windows 10/11: Rust toolchain MSVC hoặc GNU phù hợp với máy.

## Cài đặt

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.ps1 | iex
```

Installer chỉ biên dịch Rust với tối đa hai job mặc định, sau đó dùng `anoti install` để cài binary, extension và merge hook mà vẫn giữ cấu hình bên thứ ba. Trên GNOME Wayland, hãy đăng xuất rồi đăng nhập lại nếu phiên Shell hiện tại chưa nạp version extension mới; contract v6 trở lên được dùng để giữ popup không bị chìm.

## Lệnh thường dùng

```bash
anoti focus
anoti doctor
anoti status
anoti test
anoti config
anoti update
anoti uninstall
anoti --title "Xong việc" --message "Tiến trình đã hoàn tất"
```

## Tích hợp agent

- Claude Code: `SessionStart`, câu hỏi, yêu cầu quyền và hoàn thành.
- Codex: `notify`, `SessionStart` và `PermissionRequest`; hỗ trợ các khóa payload chính thức dạng `thread-id` và `turn-id`.
- Antigravity: `PreInvocation`, câu hỏi, yêu cầu quyền và hoàn thành.

Hook bắt đầu phiên chụp identity đồng bộ. Các thông báo sau đó chạy tách rời để không chặn agent. Payload không hợp lệ được xử lý fail-open.

## Cấu trúc mã nguồn

```text
crates/anoti-core              model, identity, state và queue
crates/anoti-hooks             adapter payload và merge cấu hình hook
crates/anoti-platform          hợp đồng backend
crates/anoti-platform-linux    X11, GNOME Wayland, GTK và D-Bus
crates/anoti-platform-windows  Win32 focus, monitor, overlay và toast
crates/anoti-delivery          webhook và delivery
crates/anoti-app               CLI, orchestration và lifecycle
gnome-shell-extension          adapter compositor cho GNOME Wayland
artifacts/manifest.json        danh sách artifact được quản lý
```

Xem [kiến trúc](docs/architecture.md), [phát triển Rust](docs/rust-development.md) và [hướng dẫn Windows](docs/windows-guide.md).

## Giấy phép

[MIT](LICENSE)
