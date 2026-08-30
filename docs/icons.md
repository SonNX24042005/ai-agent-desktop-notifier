# Nguồn biểu tượng và bản quyền (Icons & licensing)

Tài liệu này ghi nhận nguồn gốc, đường dẫn cố định (pinned URL), mã băm SHA-256, mã màu thương hiệu, giấy phép và các giới hạn nhãn hiệu thương mại đối với các biểu tượng thông báo được sử dụng trong `anoti`.

## 1. Claude Code
- **Mô tả**: Biểu tượng hoa thị đa tia của Claude từ thư viện Simple Icons.
- **Màu thương hiệu**: `#D97757` (theo hướng dẫn nhận diện của Simple Icons / claude.ai).
- **Đường dẫn cố định (pinned URL)**: `https://cdn.jsdelivr.net/npm/simple-icons@15.0.0/icons/claude.svg`
- **Mã băm SHA-256 (SVG nguồn)**: `2d6fda79eb18ddccca35b799eeb3cece0dfabc22520ce3b10abd25668df9fa93`
- **Ngày truy cập**: 2026-08-30.
- **Nguồn giấy phép**: [Simple Icons License (CC0 1.0 Universal)](https://cdn.jsdelivr.net/npm/simple-icons@15.0.0/LICENSE.md).
- **Giới hạn nhãn hiệu thương mại**: Giấy phép CC0 1.0 Universal chỉ áp dụng cho mã nguồn vector và không cấp quyền sử dụng nhãn hiệu thương mại. Claude là nhãn hiệu thương mại của Anthropic, PBC.

## 2. OpenAI Codex
- **Mô tả**: Biểu tượng xoắn ốc lục giác của OpenAI từ thư viện Simple Icons.
- **Màu thương hiệu**: `#412991` (theo hướng dẫn nhận diện của Simple Icons / openai.com/brand).
- **Đường dẫn cố định (pinned URL)**: `https://cdn.jsdelivr.net/npm/simple-icons@15.0.0/icons/openai.svg`
- **Mã băm SHA-256 (SVG nguồn)**: `2b4a04ddc2395b20d168694d3850ce2050a702c4a0cdeb4d8b31b9a970481a8c`
- **Ngày truy cập**: 2026-08-30.
- **Nguồn giấy phép**: [Simple Icons License (CC0 1.0 Universal)](https://cdn.jsdelivr.net/npm/simple-icons@15.0.0/LICENSE.md).
- **Giới hạn nhãn hiệu thương mại**: Giấy phép CC0 1.0 Universal chỉ áp dụng cho mã nguồn vector và không cấp quyền sử dụng nhãn hiệu thương mại. Codex và OpenAI là nhãn hiệu thương mại của OpenAI, Inc.

## 3. Google Antigravity
- **Mô tả**: Biểu tượng raster PNG chính thức từ website Google Antigravity.
- **Kích thước gốc**: 180x180 pixel.
- **Đường dẫn cố định (pinned URL)**: `https://antigravity.google/apple-touch-icon.png`
- **Mã băm SHA-256**: `81ff621394faed1deb9c5577c7d5b651c5759ce927c1594dc8f6f6382a434670`
- **Ngày truy cập**: 2026-08-30.
- **Bản quyền & lưu ý**: Google không công bố giấy phép tái phân phối nguồn mở riêng tại URL này. Bản quyền hình ảnh và nhãn hiệu thương mại thuộc Google LLC. Biểu tượng được sử dụng để nhận diện sản phẩm trong thông báo tích hợp ứng dụng theo nguyên tắc sử dụng định danh (nominative use).
- **Giới hạn nhãn hiệu thương mại**: Antigravity và Gemini là nhãn hiệu thương mại của Google LLC.

## 4. Anoti (Fallback / mặc định)
- **Mô tả**: Biểu tượng chuông thông báo vector tối giản nguyên bản của dự án `anoti`.
- **Giấy phép**: MIT License.

## 5. Quy cách kỹ thuật
- **Runtime notifications**: Sử dụng trực tiếp tệp raster PNG (`assets/icons/*.png`) cho cả Linux D-Bus và Windows Toast XML nhằm đảm bảo tính tương thích và hiển thị nhất quán trên mọi notification daemon và Windows Toast viewer.
- **Nguồn vector & scalable icons**: Lưu trữ tệp SVG nguyên bản (`assets/icons/*.svg`) phục vụ mục đích kiểm toán nguồn độc lập và cài đặt icon hicolor scalable trên Linux.
- **Phân phối runtime**: Nhúng trực tiếp vào binary thực thi qua `include_bytes!`, cài đặt đối xứng vào `.local/share/anoti/icons/` thông qua `artifacts/manifest.json` và tự động dọn dẹp khi gỡ cài đặt.
