# AI agent desktop notifier (anoti)

Hệ thống thông báo nổi đa màn hình (multi-monitor desktop notification overlay) kết hợp tự động chuyển đổi tiêu điểm cửa sổ (auto focus window) dành cho các công cụ AI coding agent: **Claude Code**, **Google Antigravity**, và **OpenAI Codex** trên cả **Linux** (X11 / GNOME) và **Windows** (10 / 11).

---

## Tính năng nổi bật

- **Hiển thị trên tất cả màn hình**: Tự động nhận diện toàn bộ màn hình đang kết nối (Xinerama/XRandR trên Linux, Win32 Monitor API trên Windows), hiển thị đồng thời banner nổi dark-slate trên từng màn hình kèm âm thanh cảnh báo hệ thống.
- **Hỗ trợ song song trên Windows (Dual Notification)**:
  - **Cửa sổ nổi đa màn hình (Tkinter overlay)**: Giao diện tối màu hiện đại, viền phát sáng theo loại thông báo, nút bấm chuyển cửa sổ và đóng nhanh, tự động co dãn theo DPI.
  - **Thông báo chuẩn Windows (Native Toast notification)**: Hiển thị thông báo góc dưới màn hình chuẩn Action Center của Windows 10/11.
- **Tự động chuyển workspace và focus cửa sổ thông minh (5 tầng nhận diện)**:
  - **Tự động chuyển đúng không gian làm việc (workspace)**: Nhận diện không gian làm việc chứa cửa sổ ứng dụng và tự động chuyển màn hình sang đúng workspace đó trước khi kích hoạt cửa sổ (Linux).
  - **Bỏ qua giới hạn foreground lock (Windows)**: Sử dụng kỹ thuật `AttachThreadInput` và `SetForegroundWindow` của Win32 API để đưa cửa sổ IDE/terminal lên đầu màn hình ngay lập tức mà không bị hiện tượng nhấp nháy thanh tác vụ.
  - **Tầng 1 (Session cache)**: Tra cứu ID cửa sổ đã lưu từ đầu phiên (`SessionStart`/`PreInvocation`) kèm kiểm tra tính hợp lệ và PID để tránh dùng lại ID cũ.
  - **Tầng 2 (Cây tiến trình PID)**: Lần ngược cây PID cha (`/proc/{pid}/stat` trên Linux hoặc Win32 Toolhelp snapshot trên Windows) kết hợp tên thư mục dự án để tìm cửa sổ terminal/IDE tương ứng.
  - **Tầng 3 (Window ID trực tiếp)**: Xác thực ID cửa sổ được truyền trực tiếp qua tham số `--window-id` (phải là cửa sổ nhà phát triển hợp lệ).
  - **Tầng 4 (Cửa sổ đang hoạt động)**: Lấy cửa sổ foreground hiện tại nếu là cửa sổ nhà phát triển phù hợp.
  - **Tầng 5 (Khớp tiêu đề cửa sổ)**: Tìm kiếm tên thư mục dự án trên tiêu đề các cửa sổ nhà phát triển đang mở.
  - **Focus native Wayland**: Trên GNOME Shell, adapter compositor đi kèm dự án đưa đúng cửa sổ lên foreground bằng identity PID, project, title và agent. Codex desktop được nhận diện qua class `Chatgpt`, còn Antigravity qua class ứng dụng riêng; adapter vẫn từ chối khi có nhiều cửa sổ cùng khớp.
- **Hàng đợi thông báo thông minh giữa nhiều cửa sổ (multi-window notification queue)**: Khi có nhiều thông báo từ các cửa sổ / phiên làm việc AI agent khác nhau, hệ thống tự động lưu vào hàng đợi kèm số đếm trạng thái (ví dụ: `[1/3]`). Thông báo hoàn thành cũng giữ identity trong lúc popup hiển thị để `Alt+Q` có thể chuyển về đúng cửa sổ agent. Sau khi giải quyết xong cửa sổ hiện tại, hệ thống sẽ tự động bật lại thông báo còn tồn đọng của cửa sổ tiếp theo để bạn không bao giờ bị bỏ sót tác vụ.
- **Tự động đóng khi người dùng chủ động mở cửa sổ (auto-dismiss on active window)**: Khi popup đang hiển thị mà bạn tự chuyển vào cửa sổ AI agent (qua chuột, thanh tác vụ hoặc `Alt + Tab`), hệ thống sẽ tự động phát hiện cửa sổ mục tiêu đã được kích hoạt và tự động đóng popup sau 1,5 giây, dọn hàng đợi mà không cần bạn phải thao tác đóng thủ công. Việc dò cửa sổ chạy ở nền để popup vẫn phản hồi khi API hệ thống chậm. Trên GNOME Wayland, engine hỏi trực tiếp compositor qua adapter `IsWindowActive`; AT-SPI chỉ là fallback khi adapter không khả dụng.
- **Chống lặp thông báo (anti-spam deduplication)**: Băm nội dung bằng SHA-256 và áp dụng khoảng thời gian làm mát (cooldown) để tránh hiện tượng bắn liên tiếp nhiều thông báo trùng lặp.
- **Chuyển tiếp đa kênh (webhooks)**: Gửi thông báo ngầm đến điện thoại hoặc kênh chat nhóm (Slack, Discord, Bark iOS, ntfy, Feishu, DingTalk) khi bạn rời khỏi bàn làm việc.
- **Tương tác nhanh và phím tắt**:
  - **Phím tắt toàn cục (`Alt + Q`)**: Đang làm việc ở bất kỳ đâu (lướt web, đọc tài liệu, soạn thảo), chỉ cần bấm `Alt + Q` (trên Linux) hoặc gọi lệnh `anoti focus` để chuyển ngay đến cửa sổ AI agent đang chờ phản hồi. Phím tắt, lệnh CLI và nút popup dùng cùng identity đã lưu trong queue.
  - **Tương tác trực tiếp trên popup**: Nhấn nút *"Đến cửa sổ (Alt+Q)"*, hoặc dùng phím tắt `Enter` / `Space` / `F` để chuyển vào ứng dụng, `Esc` / `Q` để đóng popup. Nút hiển thị *"Đang chuyển..."* khi yêu cầu focus chạy ở nền và tự bật lại nếu chưa tìm thấy cửa sổ đích.
- **Bộ công cụ CLI `anoti`**: Lệnh ngắn gọn, tiện lợi để focus cửa sổ, cập nhật, kiểm tra trạng thái, bắn thông báo thử nghiệm và gỡ cài đặt ở bất kỳ đâu trên hệ thống.

---

## Hỗ trợ các AI agent

1. **Claude Code** (tích hợp qua hook vòng đời trong `~/.claude/settings.json`: `SessionStart`, `PreToolUse: AskUserQuestion`, `Notification`, `Stop`).
2. **OpenAI Codex** (tích hợp qua cấu hình `notify` trong `~/.codex/config.toml` và `PermissionRequest` trong `~/.codex/hooks.json`).
3. **Google Antigravity** (tích hợp qua hook vòng đời `desktop-notifier` trong `~/.gemini/config/hooks.json`).

---

## Cài đặt nhanh

### Trên Windows (PowerShell)

Mở **PowerShell** (hoặc Windows Terminal) và chạy lệnh sau:

```powershell
irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.ps1 | iex
```

### Trên Linux (Ubuntu / Debian / Fedora / Arch)

Mở **Terminal** và chạy lệnh sau:

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.sh | bash
```

Sau khi cài đặt xong, hãy tải lại cửa sổ VS Code / IDE của bạn:
> `Ctrl + Shift + P` -> `Developer: Reload Window`

---

## Hướng dẫn sử dụng bộ lệnh `anoti`

Sau khi cài đặt, bạn có thể gọi lệnh `anoti` từ bất kỳ thư mục nào trên máy (hỗ trợ cả PowerShell, CMD, Git Bash, và Linux bash/zsh):

```bash
# 1. Chuyển ngay đến cửa sổ AI agent đang chờ phản hồi
anoti focus
# hoặc dùng cờ ngắn:
anoti -f

# 2. Cập nhật hệ thống thông báo lên bản mới nhất
anoti update
# hoặc dùng cờ ngắn:
anoti -u

# 3. Chẩn đoán sức khỏe hệ thống và kiểm tra đồng bộ phiên bản
anoti doctor
# hoặc dùng alias:
anoti doc

# 4. Kiểm tra trạng thái tích hợp
anoti status
# hoặc:
anoti -s

# 5. Bắn thử thông báo kiểm tra lên tất cả màn hình
anoti test
# hoặc:
anoti -t

# 6. Xem hoặc tạo file cấu hình webhook (Slack, Discord, Bark, ntfy,...)
anoti config
# hoặc:
anoti -c

# 7. Bắn thông báo tùy chỉnh từ terminal hoặc shell script
anoti --title "Xong việc" --message "Tiến trình build đã hoàn tất sau 45 giây"

# 8. Gỡ cài đặt hệ thống thông báo và khôi phục file cấu hình sạch sẽ
anoti uninstall
```

---

## Cập nhật từ xa

### Trên Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/update.ps1 | iex
```

### Trên Linux

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/update.sh | bash
```

---

## Gỡ cài đặt từ xa

### Trên Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/uninstall.ps1 | iex
```

### Trên Linux

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/uninstall.sh | bash
```

---

## Cấu hình webhook (tùy chọn)

Để nhận thông báo trên điện thoại hoặc nhóm chat khi bạn không ngồi trước máy tính, tạo file `~/.config/ai-agent-notifier/config.json` (hoặc chạy lệnh `anoti config`):

```json
{
  "webhooks": {
    "slack": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
    "discord": "https://discord.com/api/webhooks/YOUR/WEBHOOK/URL",
    "bark": "https://api.day.app/YOUR_KEY",
    "ntfy": "https://ntfy.sh/your_topic",
    "feishu": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_KEY",
    "dingtalk": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
  }
}
```

---

## Cấu trúc thư mục dự án

Tài liệu dành cho người phát triển và coding agent: [Thiết kế kiến trúc](docs/architecture.md).

```
ai-agent-desktop-notifier/
├── bin/
│   ├── anoti                     # Công cụ CLI quản lý đa nền tảng
│   ├── anoti.cmd                 # Wrapper cho Windows Command Prompt
│   ├── anoti.ps1                 # Wrapper cho PowerShell
│   └── multi-desktop-notify.py   # Engine popup đa màn hình, toast và focus cửa sổ
├── docs/
│   ├── architecture.md           # Thiết kế kiến trúc và hướng dẫn mở rộng
│   └── windows-guide.md          # Hướng dẫn chi tiết cho người dùng Windows
├── hooks/
│   ├── claude-notify.py          # Script xử lý hook vòng đời Claude Code (đa nền tảng)
│   ├── claude-notify.sh          # Script xử lý hook vòng đời Claude Code (Linux)
│   ├── codex-notify.py           # Script xử lý thông báo OpenAI Codex (đa nền tảng)
│   ├── antigravity-notify.py     # Script xử lý hook Google Antigravity (đa nền tảng)
│   └── antigravity-notify.sh     # Script xử lý hook Google Antigravity (Linux)
├── gnome-shell-extension/        # Adapter focus cửa sổ native Wayland trên GNOME Shell
├── install.ps1                   # Kịch bản cài đặt tự động trên Windows (PowerShell)
├── install.sh                    # Kịch bản cài đặt tự động trên Linux (Bash)
├── update.ps1                    # Kịch bản cập nhật trên Windows
├── update.sh                     # Kịch bản cập nhật trên Linux
├── uninstall.ps1                 # Kịch bản gỡ cài đặt trên Windows
├── uninstall.sh                  # Kịch bản gỡ cài đặt trên Linux
├── README.md                     # Tài liệu hướng dẫn sử dụng chính
├── .gitignore
└── LICENSE
```

---

## Giấy phép

Dự án được phân phối theo giấy phép mã nguồn mở [MIT License](file:///mnt/181EC3061EC2DBBE/DT/Code/PJ/ai-agent-desktop-notifier/LICENSE).
