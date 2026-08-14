# AI agent desktop notifier (anoti)

Hệ thống thông báo nổi đa màn hình (multi-monitor desktop notification overlay) kết hợp tự động chuyển đổi tiêu điểm cửa sổ (auto focus window) dành cho các công cụ AI coding agent: **Claude Code**, **Google Antigravity**, và **OpenAI Codex** trên môi trường Linux (X11 / GNOME).

---

## Tính năng nổi bật

- **Hiển thị trên tất cả màn hình**: Tự động nhận diện toàn bộ màn hình đang kết nối qua Xinerama / XRandR và hiển thị popup thông báo đồng thời trên từng màn hình, kèm âm thanh cảnh báo hệ thống.
- **Tự động focus cửa sổ thông minh (6 tầng nhận diện)**:
  - **Tầng 0 (Session cache)**: Bắt và lưu ID cửa sổ ngay khi phiên làm việc khởi động (`SessionStart`), đảm bảo tìm lại đúng cửa sổ dù người dùng đã chuyển sang ứng dụng khác.
  - **Tầng 1 (Cây tiến trình PID)**: Lần ngược cây PID cha/ông (`/proc/{pid}/stat`) và thư mục dự án để tìm cửa sổ terminal/IDE tương ứng.
  - **Tầng 2 (Khớp tiêu đề cửa sổ)**: Tìm kiếm tên thư mục dự án trên tiêu đề các cửa sổ X11.
  - **Tầng 3 (VTE title marker)**: Ghi ký tự điều khiển định danh vào TTY và quét các tab D-Bus của GNOME Terminal.
  - **Tầng 4 (Window ID trực tiếp)**: Nhận diện qua tham số `--window-id`.
  - **Tầng 5 (Cửa sổ đang hoạt động)**: Dự phòng lấy cửa sổ đang active qua `xdotool`.
- **Chống lặp thông báo (anti-spam deduplication)**: Băm nội dung bằng SHA-256 và áp dụng khoảng thời gian làm mát (cooldown) để tránh hiện tượng bắn liên tiếp nhiều thông báo trùng lặp.
- **Chuyển tiếp đa kênh (webhooks)**: Gửi thông báo ngầm đến điện thoại hoặc kênh chat nhóm (Slack, Discord, Bark iOS, ntfy, Feishu, DingTalk) khi bạn rời khỏi bàn làm việc.
- **Tương tác nhanh**: Nhấn trực tiếp vào popup, nhấn nút *"Đến cửa sổ ứng dụng"*, hoặc dùng phím tắt `Enter` / `Space` để chuyển ngay đến cửa sổ AI agent đang chờ phản hồi.
- **Bộ công cụ CLI `anoti`**: Lệnh ngắn gọn, tiện lợi để cập nhật, kiểm tra trạng thái, bắn thông báo thử nghiệm và gỡ cài đặt ở bất kỳ đâu trên hệ thống.

---

## Hỗ trợ các AI agent

1. **Claude Code** (tích hợp qua các hook vòng đời trong `~/.claude/settings.json`: `SessionStart`, `PreToolUse: AskUserQuestion`, `Notification`, `Stop`).
2. **OpenAI Codex** (tích hợp qua cấu hình `notify` trong `~/.codex/config.toml` và `PermissionRequest` trong `~/.codex/hooks.json`).
3. **Google Antigravity** (tích hợp qua hook vòng đời `desktop-notifier` trong `~/.gemini/config/hooks.json`).

---

## Cài đặt nhanh

Chạy 1 dòng lệnh sau trên terminal của bạn:

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.sh | bash
```

Sau khi cài đặt xong, hãy tải lại cửa sổ VS Code / IDE của bạn:
> `Ctrl + Shift + P` -> `Developer: Reload Window`

---

## Hướng dẫn sử dụng bộ lệnh `anoti`

Sau khi cài đặt, bạn có thể gọi lệnh `anoti` từ bất kỳ thư mục nào trên máy:

```bash
# 1. Cập nhật hệ thống thông báo lên bản mới nhất
anoti update
# hoặc dùng cờ ngắn:
anoti -u

# 2. Kiểm tra trạng thái tích hợp của các AI agent
anoti status
# hoặc:
anoti -s

# 3. Bắn thử thông báo kiểm tra lên tất cả màn hình
anoti test
# hoặc:
anoti -t

# 4. Xem hoặc tạo file cấu hình webhook (Slack, Discord, Bark, ntfy,...)
anoti config
# hoặc:
anoti -c

# 5. Bắn thông báo tùy chỉnh từ terminal hoặc shell script
anoti --title "Xong việc" --message "Tiến trình build đã hoàn tất sau 45 giây"

# 6. Gỡ cài đặt hệ thống thông báo và khôi phục file cấu hình sạch sẽ
anoti uninstall
```

---

## Cập nhật và gỡ cài đặt từ xa qua `curl`

Nếu không đứng trong thư mục dự án, bạn cũng có thể cập nhật hoặc gỡ cài đặt qua 1 dòng lệnh:

```bash
# Cập nhật nhanh
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/update.sh | bash

# Gỡ cài đặt nhanh
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

```
ai-agent-desktop-notifier/
├── bin/
│   ├── anoti                     # Công cụ CLI quản lý (update, test, status, uninstall)
│   └── multi-desktop-notify.py   # Engine popup PyGObject GTK đa màn hình và focus cửa sổ
├── hooks/
│   ├── claude-notify.sh          # Script xử lý hook vòng đời cho Claude Code
│   ├── codex-notify.py           # Script xử lý thông báo cho OpenAI Codex
│   └── antigravity-notify.sh     # Script xử lý hook vòng đời cho Google Antigravity
├── install.sh                    # Kịch bản cài đặt tự động và hợp nhất cấu hình
├── update.sh                     # Kịch bản cập nhật tự động và đồng bộ cấu hình
├── uninstall.sh                  # Kịch bản gỡ cài đặt và khôi phục cấu hình ban đầu
├── README.md                     # Tài liệu hướng dẫn sử dụng
├── .gitignore
└── LICENSE
```

---

## Kiểm tra thông báo thủ công

Bạn có thể chạy thử thông báo trực tiếp cho từng loại agent:

```bash
# Kiểm tra thông báo câu hỏi của Claude Code
echo '{"hook_event_name":"PreToolUse","tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"Câu hỏi thử nghiệm trên tất cả màn hình?"}]}}' | ~/.claude/hooks/notify-input.sh

# Kiểm tra cơ chế bắt phiên sớm của Claude Code
echo '{"hook_event_name":"SessionStart","session_id":"test-session-001"}' | ~/.claude/hooks/notify-input.sh

# Kiểm tra thông báo hoàn thành của Codex
~/.codex/notify.py '{"type":"agent-turn-complete","last-assistant-message":"Codex đã hoàn thành nhiệm vụ!"}'

# Kiểm tra thông báo câu hỏi của Antigravity
echo '{"toolCall":{"name":"ask_question","args":{"questions":[{"question":"Antigravity cần phản hồi từ bạn!"}]}}}' | ~/.gemini/hooks/notify-antigravity.sh

# Kiểm tra thông báo hoàn thành của Antigravity
echo '{"terminationReason":"model_stop"}' | ~/.gemini/hooks/notify-antigravity.sh
```

---

## Giấy phép

Dự án được phân phối theo giấy phép mã nguồn mở [MIT License](file:///mnt/181EC3061EC2DBBE/DT/Code/PJ/ai-agent-desktop-notifier/LICENSE).
