# Thiết kế kiến trúc AI agent desktop notifier

## 1. Mục đích và phạm vi

Tài liệu này mô tả kiến trúc **đang được triển khai trong mã nguồn hiện tại** của `anoti`. Đây là tài liệu định hướng cho agent và lập trình viên trước khi sửa mã, bổ sung agent mới, thay đổi cách tìm cửa sổ hoặc mở rộng backend thông báo.

Phạm vi gồm:

- adapter nhận sự kiện từ Claude Code, OpenAI Codex và Google Antigravity;
- engine thông báo dùng chung trên Linux và Windows;
- nhận diện, lưu và kích hoạt lại cửa sổ phát sinh sự kiện;
- hàng đợi, chống lặp, âm thanh, webhook và vòng đời popup;
- CLI, cài đặt, cập nhật, gỡ cài đặt và kiểm thử;
- các bất biến cần giữ và các giới hạn kỹ thuật hiện có.

Tài liệu không đề xuất viết lại hệ thống. Phần “Rủi ro và hướng cải thiện” chỉ ghi lại những điểm cần cân nhắc khi phát triển tiếp.

Nguồn sự thật theo thứ tự ưu tiên:

1. Mã nguồn và kiểm thử trong repository.
2. Tài liệu này.
3. `README.md` và tài liệu hướng dẫn theo nền tảng.

Khi hành vi thay đổi, cần cập nhật mã nguồn, kiểm thử liên quan và tài liệu này trong cùng một thay đổi.

## 2. Tóm tắt kiến trúc

Hệ thống dùng kiến trúc theo tiến trình ngắn hạn, không có daemon thường trú:

```mermaid
flowchart LR
    A[AI coding agent] --> B[Adapter hook]
    U[Người dùng hoặc phím tắt] --> C[CLI anoti]
    B -->|tiến trình tách rời và tham số CLI| E[Engine multi-desktop-notify.py]
    C --> E
    E --> S[(Trạng thái JSON trong thư mục tạm)]
    E --> R{Backend nền tảng}
    R -->|Linux| L[GTK3 overlay hoặc notify-send]
    R -->|Windows| W[Tkinter overlay và Windows toast]
    E --> H[Âm thanh và webhook]
    E --> F[Nhận diện và focus cửa sổ]
```

Các quyết định thiết kế chính:

- Hook phải thoát nhanh và không được làm hỏng vòng đời của agent.
- Mọi agent chuẩn hóa sự kiện thành cùng một hợp đồng dòng lệnh cho engine.
- Chỉ một nhóm popup được hiển thị tại một thời điểm; các yêu cầu khác nằm trong hàng đợi.
- Hệ thống ưu tiên không focus nhầm. Khi không xác định duy nhất được cửa sổ, engine trả về không có mục tiêu thay vì đoán.
- Trạng thái ngắn hạn được chia sẻ giữa các tiến trình qua tệp JSON trong thư mục tạm.
- Linux và Windows dùng chung logic nghiệp vụ, nhưng tách backend hiển thị và tích hợp hệ điều hành.

## 3. Bản đồ repository

| Đường dẫn | Trách nhiệm | Khi nào cần sửa |
| --- | --- | --- |
| `bin/multi-desktop-notify.py` | Engine trung tâm: trạng thái, chọn cửa sổ, hàng đợi, focus, popup, toast, âm thanh và webhook | Khi thay đổi hành vi runtime dùng chung hoặc backend giao diện |
| `bin/anoti` | CLI quản lý và gửi thông báo tùy chỉnh | Khi thêm lệnh cho người dùng hoặc thay đổi cách gọi engine |
| `bin/anoti.cmd`, `bin/anoti.ps1` | Wrapper để gọi CLI trên Windows | Khi cách khởi chạy CLI trên Windows thay đổi |
| `hooks/claude-notify.sh` | Adapter Claude Code được cài trên Linux | Khi payload hoặc sự kiện Claude trên Linux thay đổi |
| `hooks/claude-notify.py` | Adapter Claude Code được cài trên Windows | Khi payload hoặc sự kiện Claude trên Windows thay đổi |
| `hooks/codex-notify.py` | Adapter Codex đa nền tảng | Khi hợp đồng `notify` hoặc hook của Codex thay đổi |
| `hooks/antigravity-notify.sh` | Adapter Antigravity được cài trên Linux; tên có đuôi `.sh` nhưng nội dung là Python | Khi payload hoặc sự kiện Antigravity trên Linux thay đổi |
| `hooks/antigravity-notify.py` | Adapter Antigravity được cài trên Windows | Khi payload hoặc sự kiện Antigravity trên Windows thay đổi |
| `install.sh`, `install.ps1` | Cài file runtime, hợp nhất cấu hình agent và gửi thông báo thử | Khi thêm artifact, dependency hoặc tích hợp mới |
| `update.sh`, `update.ps1` | Lấy bản mới và đồng bộ lại cài đặt | Khi quy trình phát hành hoặc migration thay đổi |
| `uninstall.sh`, `uninstall.ps1` | Xóa artifact và cấu hình tích hợp | Khi installer bắt đầu sở hữu thêm dữ liệu |
| `tests/test_focus_identity_and_timer.py` | Kiểm thử nhận diện cửa sổ, session cache, focus, queue và timer | Khi thay đổi logic identity hoặc vòng đời popup |
| `tests/test_multi_monitor_notify.py` | Kiểm thử backend Linux và bố trí đa màn hình | Khi thay đổi chọn backend hoặc placement |

## 4. Mô hình triển khai

Repository là nguồn phát triển, nhưng hook không chạy trực tiếp từ repository. Installer sao chép artifact vào hồ sơ người dùng:

| Artifact runtime | Linux | Windows |
| --- | --- | --- |
| Engine | `~/.local/bin/multi-desktop-notify.py` | `%USERPROFILE%\.local\bin\multi-desktop-notify.py` |
| CLI | `~/.local/bin/anoti` | `%USERPROFILE%\.local\bin\anoti` và các wrapper `.cmd`, `.ps1` |
| Claude | `~/.claude/hooks/notify-input.sh` | `%USERPROFILE%\.claude\hooks\notify-claude.py` |
| Codex | `~/.codex/notify.py` | `%USERPROFILE%\.codex\notify.py` |
| Antigravity | `~/.gemini/hooks/notify-antigravity.sh` | `%USERPROFILE%\.gemini\hooks\notify-antigravity.py` |

Hệ quả quan trọng: sửa file trong repository không làm thay đổi bản đang chạy trong hồ sơ người dùng cho đến khi chạy lại installer hoặc updater.

## 5. Thành phần và trách nhiệm

### 5.1. Adapter hook

Adapter là lớp chống thay đổi giữa payload riêng của từng agent và engine dùng chung. Mỗi adapter làm sáu việc:

1. Đọc JSON từ `stdin` hoặc đối số phù hợp với agent.
2. Bỏ qua sự kiện nền, sự kiện không cần hành động và trạng thái khởi tạo im lặng.
3. Nhận diện loại sự kiện: bắt đầu phiên, câu hỏi, yêu cầu quyền hoặc hoàn thành.
4. Trích xuất `session_id`, PID gọi, cửa sổ đang hoạt động và tên dự án.
5. Chuyển payload thành tham số dòng lệnh chuẩn của engine.
6. Khởi chạy engine bất đồng bộ, chuyển `stdout`, `stderr` và `stdin` sang thiết bị rỗng.

Adapter phải ưu tiên tính an toàn cho agent: payload lỗi hoặc lỗi gửi thông báo không được làm hook trả lỗi gây gián đoạn agent.

### 5.2. Engine trung tâm

`bin/multi-desktop-notify.py` là một module đơn nhưng có các vùng trách nhiệm rõ ràng:

- khóa liên tiến trình và thao tác JSON;
- session cache và xác minh identity cửa sổ;
- chống lặp và hàng đợi;
- webhook, âm thanh và thông báo dự phòng;
- truy vấn cửa sổ và cây tiến trình theo nền tảng;
- chọn, focus và chuyển workspace;
- dựng popup Windows bằng Tkinter;
- dựng popup Linux bằng GTK3/GDK;
- điều phối CLI và vòng đời tiến trình.

Không nên đưa logic hiểu payload riêng của agent vào engine. Logic đó thuộc adapter. Ngược lại, không nên sao chép logic queue, focus hoặc render vào adapter.

### 5.3. CLI `anoti`

CLI là giao diện thao tác thủ công và quản lý vòng đời:

- `anoti focus`: focus thông báo cũ nhất đang chờ;
- `anoti test`: gửi một thông báo thử qua engine đã cài;
- `anoti status`: kiểm tra sự tồn tại của engine và cấu hình tích hợp;
- `anoti config`: tạo hoặc hiển thị cấu hình webhook;
- `anoti install`, `update`, `uninstall`: gọi script theo nền tảng;
- `anoti --title ... --message ...`: gửi thông báo tùy chỉnh.

CLI luôn tìm engine trong `~/.local/bin`, không mặc định chạy engine trong repository.

### 5.4. Script quản lý vòng đời

Installer tạo thư mục đích, sao chép artifact, cấu hình hook và gửi thông báo thử. Updater cài lại artifact từ nguồn mới. Uninstaller xóa artifact và các mục cấu hình mà dự án quản lý.

Linux đăng ký thêm phím tắt GNOME `Alt+Q` bằng `gsettings`. Windows không đăng ký phím tắt toàn cục; người dùng có thể dùng `anoti focus` hoặc giải pháp ngoài như AutoHotkey.

## 6. Hợp đồng sự kiện theo agent

| Agent | Bắt session sớm | Câu hỏi | Quyền | Hoàn thành | Ghi chú |
| --- | --- | --- | --- | --- | --- |
| Claude Code | `SessionStart` | `AskUserQuestion` | `permission_prompt` hoặc `PermissionRequest` | `Stop` hoặc `agent_completed` | Bỏ qua `idle_prompt`, `agent_needs_input` và khởi tạo agent-to-agent |
| Codex | Code adapter hỗ trợ `SessionStart` qua stdin, nhưng installer hiện chưa đăng ký sự kiện này | Chưa có nhánh câu hỏi riêng | `PermissionRequest` | `agent-turn-complete` | Lệnh `notify` có thể truyền JSON ở đối số thứ nhất; hook quyền truyền JSON qua stdin |
| Antigravity | `PreInvocation` hoặc payload có `invocationNum` | Tên tool chứa `ask` | Không tạo popup riêng trong adapter hiện tại | Chỉ khi kiểm tra `fullyIdle`, lỗi, lý do kết thúc và transcript đều cho thấy đã dừng thật | Tool call không phải câu hỏi phải trả `{"decision":"allow"}` ngay |

Payload khác nhau được chuẩn hóa thành các trường sau:

| Tham số engine | Ý nghĩa |
| --- | --- |
| `--app-name` | Tên agent dùng cho nhãn và webhook |
| `--title` | Tiêu đề đã phân loại, đồng thời đang được dùng để nhận biết thông báo hoàn thành |
| `--message` | Nội dung đã thu gọn khoảng trắng |
| `--questions-json` | Payload câu hỏi để engine trích xuất tóm tắt hiển thị |
| `--urgency` | `low`, `normal` hoặc `critical` |
| `--sound` | Đường dẫn âm thanh; Windows có thể dùng âm hệ thống khi rỗng |
| `--window-id` | ID cửa sổ đang hoạt động tại thời điểm hook chạy |
| `--caller-pid` | PID để lần ngược cây tiến trình |
| `--project-hint` | Tên thư mục dự án để đối chiếu tiêu đề cửa sổ |
| `--caller-tty` | TTY của tiến trình gọi trên Linux; hiện được adapter truyền nhưng chưa tham gia thuật toán chọn cửa sổ hiện tại |
| `--terminal-screen` | Marker GNOME Terminal; hiện được truyền nhưng chưa tham gia thuật toán chọn cửa sổ hiện tại |
| `--session-id` | Khóa ổn định để liên kết agent session với cửa sổ |
| `--timeout` | Số giây tự đóng; `0` nghĩa là không tự đóng theo timeout |

## 7. Luồng xử lý chính

### 7.1. Bắt identity đầu phiên

```mermaid
sequenceDiagram
    participant A as Agent
    participant H as Adapter hook
    participant E as Engine
    participant S as Session cache

    A->>H: Sự kiện bắt đầu phiên
    H->>H: Lấy session, PID, project và cửa sổ active
    H-->>E: --capture-session
    E->>E: Xác minh cửa sổ developer và quan hệ PID
    E->>S: Ghi identity bằng lock và atomic replace
    H-->>A: Thoát ngay
```

Mục đích của luồng này là chụp cửa sổ khi quan hệ còn rõ ràng, trước khi người dùng chuyển sang ứng dụng khác.

### 7.2. Hiển thị một thông báo

`main()` của engine thực hiện theo thứ tự:

1. Xử lý lệnh đặc biệt `focus`, `install`, `update`, `uninstall` hoặc `capture-session`.
2. Làm sạch nội dung, giới hạn mặc định còn 300 ký tự.
3. Bỏ qua bản trùng trong khoảng `dedupe_seconds`, mặc định là 2 giây.
4. Kết thúc popup cũ bằng PID file để tránh chồng nhiều nhóm popup.
5. Tìm cửa sổ mục tiêu theo thuật toán identity nghiêm ngặt.
6. Tạo khóa queue theo session, window, PID, project hoặc khóa mặc định.
7. Nếu tiêu đề không phải hoàn thành, cập nhật item vào queue; nếu là hoàn thành, xóa item cùng khóa.
8. Phát âm thanh và gửi webhook trong nền.
9. Chọn backend theo hệ điều hành và hiển thị popup.

### 7.3. Đóng, focus và chuyển thông báo

```mermaid
stateDiagram-v2
    [*] --> Pending: Lưu vào queue
    Pending --> Visible: Engine hiển thị
    Visible --> Resolved: Người dùng đóng
    Visible --> Resolved: Target active liên tục đủ thời gian
    Visible --> FocusAttempt: Người dùng chọn đến cửa sổ
    FocusAttempt --> Resolved: Focus thành công
    FocusAttempt --> Pending: Focus thất bại
    Resolved --> Next: Lấy item cũ nhất còn lại
    Next --> Visible: Khởi chạy tiến trình mới với --from-queue
    Next --> [*]: Queue rỗng
```

Khi focus bằng `anoti focus`, engine duyệt từ item cũ nhất. Item chỉ bị xóa sau khi focus đã được xác minh thành công. Nếu không có queue hợp lệ, engine thử session cache mới cập nhật gần nhất. Engine không focus một cửa sổ developer ngẫu nhiên.

Trong popup, thao tác đóng và auto-dismiss được xem là đã xử lý thông báo nên xóa item hiện tại rồi mở item kế tiếp. Thao tác focus chỉ nên xóa item khi việc focus thành công.

## 8. Thuật toán nhận diện cửa sổ

`find_target_window()` dùng thứ tự hiện tại sau:

1. **Session cache**: lấy window ID đã lưu, kiểm tra cửa sổ còn tồn tại, thuộc ứng dụng developer và PID chưa bị tái sử dụng.
2. **Cây PID**: liệt kê các cửa sổ developer có PID trong chuỗi tổ tiên của tiến trình hook. Chấp nhận khi chỉ có một kết quả; nếu có nhiều kết quả, yêu cầu `project_hint` hoặc `window_id` tạo ra lựa chọn duy nhất.
3. **Tên dự án**: tìm `project_hint` trong tiêu đề toàn bộ cửa sổ developer. Chỉ chấp nhận đúng một kết quả.
4. **Window ID trực tiếp**: kiểm tra cửa sổ hợp lệ, thuộc ứng dụng developer và không mâu thuẫn với cây PID.
5. **GNOME Terminal trên Wayland**: nếu cây tiến trình cho thấy GNOME Terminal, dùng token đặc biệt `wayland:gnome-terminal` và kích hoạt qua D-Bus.
6. **Không đoán**: trả chuỗi rỗng khi còn mơ hồ.

Danh sách cho phép và loại trừ nằm trong `DEVELOPER_CLASSES`, `EXCLUDED_CLASSES` và `WIN_DEVELOPER_EXES`. Khi hỗ trợ IDE hoặc terminal mới, cần cập nhật nhận diện trên cả Linux và Windows, rồi bổ sung kiểm thử tránh false positive.

### 8.1. Xác minh focus

Trên Windows, engine:

- khôi phục cửa sổ nếu đang thu nhỏ;
- dùng `AttachThreadInput`, `BringWindowToTop`, `SetForegroundWindow` và `SetActiveWindow`;
- kiểm tra lại foreground window trong tối đa khoảng 0,4 giây.

Trên Linux X11/XWayland, engine:

- chuyển sang workspace chứa cửa sổ;
- thử GDK X11, `wmctrl` và `xdotool`;
- kiểm tra lại active window trong tối đa khoảng 0,4 giây.

Trên GNOME Wayland thuần, token GNOME Terminal được kích hoạt qua `gdbus`. Với trường hợp compositor không cho phép đọc active window, tính hợp lệ của target là tín hiệu dự phòng.

## 9. Trạng thái và dữ liệu runtime

### 9.1. Tệp trạng thái

| Tệp | Nội dung | Chính sách vòng đời |
| --- | --- | --- |
| `ai_agent_notifier.pid` | PID popup hiện tại | Ghi đè khi có thông báo mới |
| `ai_agent_notifier_sessions.json` | Ánh xạ session sang identity cửa sổ | Giữ tối đa 64 entry, loại entry quá 24 giờ |
| `ai_agent_notifier_sessions.lock` | Lock cho session cache | `flock` trên Linux, thư mục lock quay vòng trên Windows |
| `ai_agent_notifier_dedupe.json` | SHA-256 của app, title, message và thời điểm gần nhất | Loại key cũ hơn 60 giây khi có lần kiểm tra mới |
| `ai_agent_notifier_queue.json` | Các thông báo đang chờ theo identity | Bỏ qua item cũ hơn 4 giờ khi đọc |
| `ai_agent_notifier_queue.lock` | Đã khai báo cho queue | Chưa được các hàm queue hiện tại sử dụng |

Linux dùng `/tmp`; Windows dùng `%TEMP%` hoặc `%TMP%`.

### 9.2. Schema session cache

```json
{
  "session-id": {
    "window_id": "12345",
    "project_hint": "project-name",
    "pid": 1234,
    "app_hint": "",
    "title_fingerprint": "",
    "precision": "window",
    "backend": "x11",
    "updated_at": 1770000000.0
  }
}
```

`precision="window"` không được ghi đè bằng identity độ chính xác thấp hơn. `window_id` cũng có thể là token `wayland:gnome-terminal`.

### 9.3. Schema queue

```json
{
  "sess_example": {
    "key": "sess_example",
    "app_name": "Claude Code",
    "title": "Claude Code: Câu hỏi",
    "message": "Nội dung đã làm sạch",
    "questions_json": "{}",
    "urgency": "critical",
    "sound": "/path/to/sound.oga",
    "target_window_id": "12345",
    "caller_pid": 1234,
    "project_hint": "project-name",
    "session_id": "example",
    "timeout": 0,
    "created_at": 1770000000.0
  }
}
```

Thứ tự ưu tiên của khóa queue là `session_id` → `window_id` → `caller_pid` → `project_hint` → `default_target`. Một sự kiện mới cùng khóa thay thế item cũ thay vì tạo thêm bản sao.

### 9.4. Cấu hình webhook

Cấu hình bền vững duy nhất do runtime đọc là:

- Linux: `~/.config/ai-agent-notifier/config.json`;
- Windows: `%USERPROFILE%\.config\ai-agent-notifier\config.json`.

Schema:

```json
{
  "webhooks": {
    "slack": "",
    "discord": "",
    "bark": "",
    "ntfy": "",
    "feishu": "",
    "dingtalk": ""
  }
}
```

Mỗi webhook chạy nối tiếp trong một daemon thread, timeout từng request là 3 giây. Lỗi được bỏ qua và không có retry.

## 10. Backend giao diện và tích hợp nền tảng

| Khả năng | Linux X11/XWayland | Linux Wayland thuần | Windows 10/11 |
| --- | --- | --- | --- |
| Overlay | GTK3/GDK | GTK3 nếu khởi tạo được | Tkinter |
| Đặt đúng từng màn hình | Có, mỗi monitor một cửa sổ | Không bảo đảm; chỉ tạo một cửa sổ để tránh cascade | Có, dùng Win32 monitor API |
| Không giành focus | `accept_focus=false`, notification window hint | Phụ thuộc compositor | `WS_EX_NOACTIVATE` |
| Popup dự phòng | `notify-send` | `notify-send` | Windows toast |
| Focus | GDK X11, `wmctrl`, `xdotool` | D-Bus cho GNOME Terminal | Win32 API |
| Chuyển workspace | `wmctrl` | Bị giới hạn bởi compositor | Không áp dụng |
| Âm thanh | `paplay`, `pw-play`, `canberra-gtk-play` hoặc `aplay` | Tương tự | `winsound` khi có `--sound`; toast còn tuân theo cài đặt âm thanh hệ thống |

Backend Linux có hai biến override phục vụ chẩn đoán:

- `NOTIFY_BACKEND=x11|xwayland|wayland`;
- `NOTIFY_FORCE_WAYLAND=1`.

`DEBUG_NOTIFY=1` in thông tin backend, số monitor và tọa độ placement.

## 11. Phụ thuộc runtime

### 11.1. Phụ thuộc bắt buộc hoặc gần bắt buộc trên Linux

- Python 3;
- `jq` cho adapter shell của Claude;
- `xdotool` và `xprop` cho nhận diện cửa sổ X11;
- PyGObject với GTK3/GDK cho overlay;
- `notify-send` cho fallback;
- ít nhất một trình phát âm thanh nếu cần âm thanh;
- `wmctrl` để chuyển workspace và tăng độ tin cậy khi focus;
- `gdbus` cho GNOME Terminal trên Wayland;
- `gsettings` nếu muốn đăng ký `Alt+Q` trên GNOME.

Installer Linux hiện kiểm tra một phần các dependency này và chỉ hướng dẫn người dùng cài, không tự cài package hệ thống.

### 11.2. Phụ thuộc trên Windows

- Windows 10 hoặc 11;
- Python 3.8 trở lên có Tkinter;
- PowerShell 5.1 trở lên;
- các API Win32 và Windows Runtime có sẵn trong hệ điều hành.

## 12. Chính sách lỗi và khả năng quan sát

Thiết kế hiện tại ưu tiên “thông báo không được làm gián đoạn agent”:

- adapter và engine bắt phần lớn exception rồi thoát mã `0`;
- tiến trình popup được tách khỏi hook;
- âm thanh, toast và webhook chạy nền;
- khi overlay không khởi tạo được, engine thử thông báo hệ thống;
- khi focus thất bại, queue item được giữ lại trong luồng `anoti focus`.

Đổi lại, lỗi runtime thường không xuất hiện trên terminal. Khả năng quan sát hiện có gồm:

- `DEBUG_NOTIFY=1` cho backend Linux;
- `/tmp/antigravity_hook_debug.log` hoặc file cùng tên trong `%TEMP%` cho raw payload Antigravity;
- `anoti status` để kiểm tra cấu hình ở mức tồn tại.

Không nên thay đổi hook sang fail-closed. Nếu bổ sung logging, cần giới hạn kích thước, loại bỏ dữ liệu nhạy cảm và không chặn tiến trình agent.

## 13. Kiểm thử

Chạy toàn bộ kiểm thử hiện có bằng một worker:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Phạm vi hiện tại:

- quan hệ PID và identity session;
- atomic write, JSON lỗi, giới hạn tuổi và số lượng session;
- từ chối target mơ hồ và stale window handle;
- chỉ xóa queue sau khi focus thành công trong luồng CLI;
- timer auto-dismiss và hành vi reset;
- cờ không giành focus;
- token và D-Bus GNOME Terminal trên Wayland;
- chọn backend Linux;
- placement với monitor âm tọa độ, portrait, 4K, work area và nhiều monitor;
- fallback từ X11 sang Wayland khi GTK không khởi tạo được.

Các vùng chưa có kiểm thử tích hợp trực tiếp:

- parser payload và tính tương đương giữa các adapter;
- tương tác thật với Win32, GTK, X11, D-Bus và Windows toast;
- tranh chấp nhiều tiến trình trên queue và dedupe cache;
- installer, updater, uninstaller và bảo toàn cấu hình người dùng;
- định dạng request của từng webhook;
- vòng đời hoàn chỉnh từ hook đến popup rồi chuyển sang item tiếp theo.

## 14. Bất biến dành cho người sửa mã

Mọi thay đổi phải giữ các điều kiện sau, trừ khi có quyết định thiết kế mới được ghi rõ:

1. Hook không chờ popup đóng và không làm agent thất bại khi notifier lỗi.
2. Engine không focus một cửa sổ chỉ vì đó là IDE hoặc terminal duy nhất đang mở; identity phải có bằng chứng liên kết với session, PID, project hoặc window ID.
3. Session ID là identity ưu tiên cao nhất và phải ổn định xuyên suốt một lượt agent.
4. Popup không tự giành focus khi xuất hiện.
5. Tại một thời điểm chỉ có một nhóm popup logic; một nhóm có thể gồm một cửa sổ trên mỗi monitor.
6. Item queue không được mất khi lệnh focus không xác minh được thành công.
7. Dữ liệu state hỏng phải suy giảm về giá trị rỗng thay vì làm crash hook.
8. Backend không hỗ trợ placement không được tạo nhiều cửa sổ chồng lên cùng một monitor.
9. Mọi artifact installer thêm vào phải có đường cập nhật và gỡ tương ứng trên cả hai nền tảng.
10. Thay đổi hợp đồng tham số engine phải được áp dụng cho mọi adapter, CLI và đường `--from-queue`.

## 15. Hướng dẫn mở rộng

### 15.1. Thêm một AI agent

1. Xác định các sự kiện bắt đầu phiên, câu hỏi, quyền và hoàn thành thật sự.
2. Viết adapter chỉ để parse, lọc và chuẩn hóa payload.
3. Bắt sớm `session_id`, PID, project và window ID; gọi `--capture-session` nếu nền tảng agent cho phép.
4. Khởi chạy engine tách rời và luôn bảo vệ agent trước lỗi notifier.
5. Thêm artifact và cấu hình vào installer, updater, uninstaller cho Linux và Windows.
6. Thêm kiểm thử payload cho cả trường hợp hợp lệ, không cần thông báo và payload lỗi.
7. Cập nhật bảng hợp đồng sự kiện trong tài liệu này.

### 15.2. Thêm IDE hoặc terminal

1. Bổ sung class, executable hoặc pattern title tối thiểu cần thiết.
2. Kiểm tra cả hàm liệt kê window và hàm xác thực một window.
3. Bổ sung test positive và negative để không nhận nhầm browser hoặc ứng dụng chat.
4. Kiểm tra target mơ hồ vẫn bị từ chối.
5. Kiểm thử focus thật trên nền tảng liên quan.

### 15.3. Thêm trường cho thông báo

1. Cập nhật parser trong engine.
2. Cập nhật mọi adapter.
3. Lưu trường đó trong queue item nếu cần tồn tại qua lần bật lại.
4. Chuyển trường qua `pop_next_notification_async()`.
5. Cập nhật cả backend Windows và Linux.
6. Thêm kiểm thử round-trip qua queue.

### 15.4. Thêm backend giao diện

Backend mới phải nhận cùng contract của `show_multi_monitor_popup()`, hỗ trợ đóng, focus, auto-dismiss, timeout và chuyển queue. Nếu không thể đặt cửa sổ chính xác, backend phải giảm số popup thay vì tạo nhiều popup trùng vị trí.

### 15.5. Thay đổi installer

Kiểm tra đủ bốn chiều:

- cài mới;
- cài đè trên cấu hình đã có;
- cập nhật từ phiên bản trước;
- gỡ cài đặt mà không làm mất cấu hình không thuộc dự án.

Không coi bản sao trong repository là runtime đã được cập nhật; luôn kiểm tra artifact trong hồ sơ người dùng.

## 16. Rủi ro và hướng cải thiện

Các mục dưới đây phản ánh implementation hiện tại, theo mức ưu tiên đề xuất.

### 16.1. Ưu tiên cao

- **Bảo toàn cấu hình bên thứ ba**: installer gán lại toàn bộ `hooks` của Claude, còn uninstaller xóa toàn bộ `hooks` của Claude và Antigravity settings. Điều này có thể xóa hook không thuộc dự án. Nên quản lý từng entry theo ownership marker và chỉ sửa entry do `anoti` sở hữu.
- **Tính nguyên tử của queue và dedupe**: queue và dedupe đang dùng read-modify-write không lock, dù đã khai báo `QUEUE_LOCK_FILE`. Nhiều hook đồng thời có thể ghi đè hoặc làm mất item. Nên dùng cùng mẫu lock cộng atomic replace như session cache.
- **Dữ liệu nhạy cảm trong log**: adapter Antigravity ghi nguyên raw payload vào log không giới hạn kích thước. Payload có thể chứa prompt, đường dẫn hoặc tham số tool. Nên tắt mặc định, redaction và xoay vòng log.

### 16.2. Ưu tiên trung bình

- **Trùng lặp adapter**: biến thể Linux và Windows của Claude, Antigravity có logic song song và có thể lệch hành vi. Nên dùng một core Python chung, wrapper nền tảng chỉ thiết lập môi trường.
- **Phân loại completion bằng title**: engine quyết định có lưu queue hay không bằng từ khóa trong tiêu đề. Đây là hợp đồng ngầm, phụ thuộc ngôn ngữ. Nên truyền loại sự kiện rõ ràng, ví dụ `--event-kind=completion`.
- **Quan sát lỗi**: nhiều `except Exception: pass` làm khó chẩn đoán. Nên có logging opt-in, có giới hạn và không chứa bí mật.
- **Webhook**: URL lưu dạng rõ, không redaction payload, không retry và gửi nối tiếp. Cần mô hình threat và chính sách dữ liệu trước khi mở rộng.
- **Đồng bộ tài liệu**: README mô tả một số tầng fallback cũ, trong khi code hiện tại chủ động từ chối active window ngẫu nhiên. Tài liệu người dùng nên được cập nhật theo thuật toán nghiêm ngặt trong mục 8.

### 16.3. Ưu tiên thấp

- Tách engine lớn thành module state, identity, delivery và UI để dễ kiểm thử.
- Bổ sung manifest dependency hoặc kiểm tra môi trường nhất quán trên các distro.
- Thêm CI cho unit test và kiểm tra cú pháp Bash, Python, PowerShell.
- Thêm migration version cho schema state nếu cấu trúc JSON tiếp tục phát triển.

## 17. Checklist trước khi hoàn tất một thay đổi

- [ ] Hành vi đã được đối chiếu trên Linux và Windows.
- [ ] Adapter không chặn hoặc trả lỗi cho agent.
- [ ] Không tạo đường focus mơ hồ hoặc focus nhầm.
- [ ] Queue giữ được item khi focus thất bại.
- [ ] Trường mới được truyền qua đường `--from-queue`.
- [ ] Installer, updater và uninstaller cùng quản lý artifact mới.
- [ ] Cấu hình không thuộc dự án được bảo toàn.
- [ ] Kiểm thử mục tiêu đã chạy với số worker giới hạn.
- [ ] Tài liệu này và README liên quan đã được cập nhật.
