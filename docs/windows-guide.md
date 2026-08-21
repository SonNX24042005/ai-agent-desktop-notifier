# Hướng dẫn sử dụng trên hệ điều hành Windows

Tài liệu hướng dẫn chi tiết cách cài đặt, cấu hình và sử dụng **AI Agent Desktop Notifier (`anoti`)** trên môi trường **Windows 10** và **Windows 11**.

---

## 1. Yêu cầu hệ thống

- **Hệ điều hành**: Windows 10 (bản 1809 trở lên) hoặc Windows 11.
- **Python**: Phiên bản Python 3.8 trở lên (đã bao gồm thư viện `tkinter` và `ctypes` đi kèm mặc định trong gói cài đặt Python chính thức).
- **PowerShell**: PowerShell 5.1 trở lên (mặc định có sẵn trên Windows 10/11) hoặc PowerShell 7+.

Nếu máy chưa có Python, bạn có thể cài đặt nhanh qua lệnh:

```powershell
winget install Python.Python.3.12
```

---

## 2. Cài đặt tự động

Mở **PowerShell** hoặc **Windows Terminal** và chạy lệnh cài đặt 1 dòng sau:

```powershell
irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.ps1 | iex
```

### Các bước script tự động thực hiện:

1. Kiểm tra môi trường Python trên hệ thống.
2. Tạo các thư mục cần thiết trong `%USERPROFILE%` (`.local\bin`, `.claude\hooks`, `.codex`, `.gemini\hooks`, `.gemini\config`).
3. Tự động thêm `%USERPROFILE%\.local\bin` vào biến môi trường `Path` của người dùng để gọi được lệnh `anoti` ở mọi nơi.
4. Tích hợp hook thông báo cho **Claude Code**, **OpenAI Codex** và **Google Antigravity**.
5. Bắn thông báo thử nghiệm kèm âm thanh để xác nhận hệ thống hoạt động bình thường.

Sau khi hoàn tất, hãy reload lại cửa sổ VS Code / IDE:
> Nhấn `Ctrl + Shift + P` -> gõ `Developer: Reload Window` -> nhấn Enter.

---

## 3. Cơ chế hoạt động trên Windows

Hệ thống cung cấp cơ chế thông báo kép (dual notification) tối ưu cho Windows:

1. **Popup nổi đa màn hình (Tkinter overlay)**:
   - Tự động nhận diện độ phân giải và vị trí của tất cả màn hình phụ và màn hình chính đang kết nối.
   - Hiển thị banner nổi dark-slate ở giữa phía trên từng màn hình.
   - Tự động điều chỉnh kích thước theo mật độ điểm ảnh (DPI awareness).
   - Hiển thị badge phân loại (`CÂU HỎI`, `CẦN CẤP QUYỀN`, `HOÀN THÀNH`), số đếm hàng đợi (ví dụ: `[1/3]`) và nội dung tóm tắt.
   - Nút bấm trực quan *"Đến cửa sổ [Alt+Q]"* và *"✕ Đóng [Esc]"*.

2. **Thông báo hệ thống (Windows native toast)**:
   - Đồng thời gửi thông báo chuẩn vào Action Center / Toast của Windows 10/11 trong nền.

3. **Chuyển tiêu điểm cửa sổ (auto focus window)**:
   - Sử dụng Win32 API (`ctypes.windll.user32`) với kỹ thuật `AttachThreadInput` để giải phóng foreground lock của Windows, đưa đúng cửa sổ VS Code, Cursor hoặc Windows Terminal lên phía trước mà không bị hiện tượng nhấp nháy thanh tác vụ.
   - Lần ngược cây tiến trình (process tree climbing) qua Toolhelp32Snapshot để xác định chính xác cửa sổ gốc phát sinh thông báo.

4. **Tự động đóng khi người dùng chủ động mở cửa sổ**:
   - Khi popup đang hiển thị mà bạn tự chuyển vào cửa sổ AI agent (qua chuột, thanh tác vụ hoặc `Alt + Tab`), hệ thống sẽ tự động phát hiện và đóng popup sau 1.5 giây mà không cần bấm nút đóng thủ công.

---

## 4. Bảng phím tắt trên popup

Khi popup thông báo xuất hiện trên màn hình, bạn có thể thao tác nhanh bằng bàn phím:

| Phím tắt | Chức năng |
| :--- | :--- |
| `Enter` / `Space` / `F` / `Y` | Chuyển ngay đến cửa sổ ứng dụng và đóng popup |
| `Esc` / `Q` / `N` | Đóng popup thông báo hiện tại |
| Click chuột vào nút xanh | Chuyển ngay đến cửa sổ ứng dụng |
| Click chuột vào nút xám hoặc ngoài viền | Đóng popup |

---

## 5. Tạo phím tắt toàn cục trên Windows

Nếu bạn muốn bấm phím tắt (ví dụ `Alt + Q` hoặc `Win + Q`) ở bất kỳ đâu trong Windows để chuyển ngay vào cửa sổ AI agent đang chờ phản hồi, bạn có thể tạo một script AutoHotkey nhỏ:

1. Cài đặt [AutoHotkey](https://www.autohotkey.com/).
2. Tạo file `anoti-shortcut.ahk` với nội dung:

```autohotkey
!q:: ; Phím tắt Alt + Q
Run, anoti focus, , Hide
return
```

3. Chuột phải vào file `.ahk` chọn **Run script** (hoặc copy file vào thư mục `shell:startup` để tự khởi động cùng Windows).

---

## 6. Bộ lệnh CLI `anoti` trên Windows

Bạn có thể gọi trực tiếp `anoti` từ PowerShell hoặc Command Prompt:

```powershell
# Chuyển đến cửa sổ AI agent đang chờ
anoti focus

# Bắn thông báo thử nghiệm
anoti test

# Kiểm tra trạng thái tích hợp
anoti status

# Cập nhật phiên bản mới nhất
anoti update

# Xem hoặc sửa cấu hình webhook
anoti config

# Gỡ cài đặt sạch sẽ
anoti uninstall
```

---

## 7. Xử lý sự cố thường gặp (Troubleshooting)

### Không nhận lệnh `anoti` trong terminal mới
- Nếu bạn vừa cài đặt xong mà PowerShell báo lỗi không nhận lệnh `anoti`, hãy tắt và mở lại cửa sổ PowerShell để Windows nạp lại biến môi trường `Path` mới.

### Thông báo không phát ra âm thanh
- Trên Windows, âm thanh mặc định sử dụng âm thanh hệ thống (System Asterisk / Exclamation). Hãy đảm bảo âm lượng hệ thống của bạn đang được bật.

### Cửa sổ popup bị ẩn sau một số ứng dụng toàn màn hình độc quyền
- Hầu hết các game hoặc ứng dụng ở chế độ Exclusive Fullscreen sẽ chiếm trọn màn hình hiển thị. Khi đó thông báo Windows Native Toast ở góc màn hình sẽ đóng vai trò dự phòng đảm bảo bạn không bỏ lỡ thông báo.
