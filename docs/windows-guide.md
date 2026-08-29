# Hướng dẫn Windows

## Yêu cầu

- Windows 10 hoặc Windows 11.
- PowerShell 5.1 trở lên.
- Git và Rust toolchain có Cargo.

Python không còn là dependency runtime.

## Cài đặt

```powershell
irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.ps1 | iex
```

Installer tải mã nguồn khi chạy từ xa, build `anoti.exe` với tối đa hai job mặc định, cài binary vào `%USERPROFILE%\.local\bin` và merge hook của Claude Code, Codex, Antigravity.

## Hoạt động

Backend Windows dùng Win32 để:

- chụp HWND, PID và tiêu đề cửa sổ đầu phiên;
- phân biệt nhiều cửa sổ cùng ứng dụng bằng HWND exact;
- phục hồi cửa sổ thu nhỏ và xác minh foreground sau focus;
- lấy monitor và DPI cho overlay;
- gửi Windows toast làm đường thông báo native.

Mỗi popup dùng identity snapshot trong queue và tự đóng sau khi cửa sổ đích active ổn định.

## Lệnh

```powershell
anoti focus
anoti doctor
anoti status
anoti test
anoti config
anoti update
anoti uninstall
```

Nếu PowerShell mới chưa nhận `anoti`, hãy mở terminal mới và bảo đảm `%USERPROFILE%\.local\bin` có trong `Path`.

Mã Windows được kiểm tra bằng unit test/mock khi phát triển trên Linux. Chỉ coi là đã xác minh native sau khi chạy bộ smoke test trên Windows thật.
