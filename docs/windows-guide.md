# Hướng dẫn Windows

## Yêu cầu

- Windows 10 hoặc Windows 11.
- PowerShell 5.1 trở lên.
- Git và Rust toolchain có Cargo.

## Cài đặt

```powershell
irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.ps1 | iex
```

Bộ cài đặt tải mã nguồn khi chạy từ xa, build `anoti.exe` với tối đa hai job mặc định, cài binary vào `%USERPROFILE%\.local\bin` và cấu hình hook của Claude Code, Codex, Antigravity.

## Hoạt động

Backend Windows sử dụng WinRT/Win32 để:

- phát Windows toast notification native chuẩn;
- phát âm thanh thông báo hệ thống qua `PlaySoundW`;
- chống trùng lặp thông báo trong 2 giây;
- gửi webhook bất đồng bộ tới các dịch vụ từ xa nếu được cấu hình.

## Lệnh

```powershell
anoti doctor
anoti status
anoti test
anoti config
anoti update
anoti uninstall
```

Nếu PowerShell mới chưa nhận `anoti`, hãy mở terminal mới và bảo đảm `%USERPROFILE%\.local\bin` có trong `Path`.

Mã Windows được kiểm tra bằng unit test và mock API khi phát triển trên Linux. Chỉ coi là đã xác minh native sau khi chạy kiểm tra trên Windows thật.
