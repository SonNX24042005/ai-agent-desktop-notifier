# Quy tắc phát triển dự án

## Hỗ trợ đa nền tảng

1. Không hardcode implementation cho duy nhất một hệ điều hành khi chức năng thuộc luồng dùng chung của dự án.
2. Mọi thay đổi phải duy trì hỗ trợ đa nền tảng trong phạm vi dự án, tối thiểu gồm Linux và Windows. Không được sửa để nền tảng đang sử dụng hoạt động nhưng làm hỏng nền tảng còn lại.
3. Không hardcode đường dẫn, lệnh shell, biến môi trường, định dạng path, API cửa sổ, cơ chế thông báo hoặc dependency chỉ tồn tại trên một nền tảng vào logic dùng chung.
4. Logic riêng theo nền tảng phải được cô lập sau bước nhận diện rõ ràng như `sys.platform`, `os.name` hoặc cơ chế tương đương. Khi phù hợp, phải có fallback an toàn cho nền tảng không hỗ trợ API chính.
5. Khi thay đổi engine, hợp đồng CLI, adapter hook, cấu hình runtime hoặc artifact cài đặt, phải rà soát tác động trên cả Linux và Windows. Nếu thêm artifact mới, phải cập nhật đường cài đặt, cập nhật và gỡ cài đặt tương ứng cho từng nền tảng.
6. Kiểm thử phải nhận diện hệ điều hành thực tế của môi trường đang chạy và thực thi các kiểm thử phù hợp với nền tảng đó. Không được giả định cứng môi trường kiểm thử là Linux hoặc Windows.
7. Không được chạy trực tiếp lệnh hoặc binary dành cho hệ điều hành khác với môi trường hiện tại. Kiểm thử cho nền tảng không hiện diện phải dùng unit test với mock, abstraction hoặc điều kiện skip rõ ràng.
8. Không được báo đã kiểm thử thực tế một nền tảng nếu môi trường hiện tại không cung cấp nền tảng đó. Phải nêu rõ phần nào đã chạy thật, phần nào chỉ được kiểm tra bằng mock hoặc chưa thể xác minh.
9. Kiểm thử dùng chung phải tiếp tục chạy được trên mọi nền tảng được hỗ trợ. Kiểm thử riêng theo nền tảng phải có guard nhận diện nền tảng và lý do skip cụ thể.

## Đồng bộ thay đổi kiến trúc

1. Một thay đổi được xem là ảnh hưởng kiến trúc khi nó thay đổi ít nhất một trong các nội dung sau:
   - ranh giới hoặc trách nhiệm giữa các thành phần;
   - entry point, adapter hook hoặc luồng sự kiện;
   - hợp đồng CLI, payload hoặc cấu hình;
   - schema state, queue, session cache hoặc quy tắc identity;
   - thuật toán chọn và focus cửa sổ;
   - backend giao diện, fallback hoặc nền tảng được hỗ trợ;
   - mô hình cài đặt, cập nhật, gỡ cài đặt hoặc triển khai runtime.
2. Khi sửa mã có ảnh hưởng kiến trúc, phải cập nhật đồng bộ trong cùng một thay đổi:
   - implementation trên mọi nền tảng bị ảnh hưởng;
   - kiểm thử liên quan;
   - adapter, CLI và script `install`, `update`, `uninstall` nếu hợp đồng hoặc artifact thay đổi;
   - `docs/architecture.md`;
   - `README.md` và tài liệu dành cho người dùng nếu hành vi công khai thay đổi.
3. Không được hoàn tất hoặc bàn giao thay đổi khi tài liệu kiến trúc vẫn mô tả hành vi cũ, schema cũ hoặc luồng xử lý không còn đúng với mã nguồn.
4. Nếu xác định thay đổi không cần cập nhật một thành phần đồng bộ nêu trên, phải ghi rõ lý do trong báo cáo hoàn thành.
5. Khi cập nhật kiến trúc, phải giữ mã nguồn và kiểm thử là nguồn sự thật chính; tài liệu phải mô tả implementation thực tế, không mô tả hành vi dự kiến nhưng chưa được triển khai.

