# TDTU Calendar Sync

Tự động lấy lịch học, lịch thi và deadline eLearning từ cổng TDTU, sau đó đồng bộ trực tiếp vào Google Calendar. Google Calendar là nơi lưu trữ duy nhất.

## Luồng hoạt động

```text
run_hour.py
  └─ crawler.py (TDTU / eLearning)
       └─ calendar_sync.py → Google Calendar

daily_notify.py
  ├─ Google Tasks (OAuth) → việc chưa làm
  ├─ calendar_sync.py (đọc Google Calendar) → lịch hôm nay
  └─ ntfy push
```

- Lịch học dùng thẻ nội bộ `class_session`.
- Lịch thi dùng thẻ `exam` và tiêu đề `[EXAM]`.
- Deadline dùng thẻ `deadline` và tiêu đề `[DEADLINE]`.

## Cài đặt local

1. Tạo môi trường Python và cài dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   ```

2. Điền các biến sau trong `.env`:

   ```dotenv
   STUDENT_ID=...
   PASSWORD=...
   GOOGLE_CALENDAR_ID=your_calendar_id
   GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
   ```

3. Chia sẻ Google Calendar đích cho `client_email` trong `service-account.json` với quyền **Make changes to events**. Không dùng `GOOGLE_CALENDAR_ID=primary`.

4. Chạy đồng bộ:

   ```bash
   python run_hour.py
   ```

## Chạy tự động

Workflow `Hourly Schedule Sync` chạy:

- Mỗi giờ;
- Mỗi lần push lên nhánh `main`;
- Khi bấm **Run workflow** trên GitHub.

Thêm các GitHub secrets: `STUDENT_ID`, `PASSWORD`, `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`. Giá trị `GOOGLE_SERVICE_ACCOUNT_JSON` là toàn bộ nội dung JSON của service account, không phải đường dẫn tệp local.

## Thông báo buổi sáng (ntfy)

`daily_notify.py` gửi mỗi sáng một thông báo gồm **việc chưa làm** (từ Google
Tasks) và **lịch hôm nay** (từ Google Calendar), push lên điện thoại qua
[ntfy](https://ntfy.sh).

Workflow `Daily Schedule Notification` chạy lúc **05:00 giờ Việt Nam** (22:00 UTC hôm trước).

### Google Tasks (OAuth)

Google Tasks **không đọc được bằng service account** — task list là dữ liệu cá
nhân và không có cơ chế chia sẻ. Phải dùng OAuth với chính tài khoản Google
của bạn.

Thực hiện một lần:

1. Bật **Google Tasks API** cho project của bạn (thay `YOUR_PROJECT_NUMBER`):
   <https://console.developers.google.com/apis/api/tasks.googleapis.com/overview?project=YOUR_PROJECT_NUMBER>
2. Tạo **OAuth client ID** (loại *Desktop app*) trong cùng project, lấy
   Client ID và Client Secret.
3. Điền `GOOGLE_TASKS_CLIENT_ID` và `GOOGLE_TASKS_CLIENT_SECRET` vào `.env`.
4. Lấy refresh token:

   ```bash
   python auth_google_tasks.py url       # mở link, đăng nhập, cấp quyền
   python auth_google_tasks.py exchange  # dán code lấy được
   python auth_google_tasks.py test      # kiểm tra đọc được task
   ```

   Refresh token không hết hạn nên chỉ làm một lần.

5. Thêm các GitHub secrets: `GOOGLE_TASKS_CLIENT_ID`,
   `GOOGLE_TASKS_CLIENT_SECRET`, `GOOGLE_TASKS_REFRESH_TOKEN`, `NTFY_TOPIC`,
   `NTFY_TOKEN`, `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`.

Nếu thiếu cấu hình Tasks, thông báo vẫn gửi được, chỉ bỏ qua phần việc chưa làm.

### Cấu hình

1. Cài app ntfy trên Android (Google Play hoặc F-Droid).
2. Trong app, chọn server `https://ntfy.sh` rồi thêm topic trùng với `NTFY_TOPIC`.
   Topic là mật khẩu — không chia sẻ.
3. (Khuyến nghị) Tạo tài khoản miễn phí tại <https://ntfy.sh>, vào
   **Account → Access tokens** để lấy token `tk_...` và điền vào `NTFY_TOKEN`.

   Không có token thì ntfy giới hạn theo **IP**, mà IP của GitHub Actions dùng
   chung với nhiều người khác, nên dễ chạm giới hạn 250 tin/ngày và nhận lỗi 429.
   Có token thì giới hạn theo tài khoản.

4. Thêm các GitHub secrets: `NTFY_TOPIC`, `NTFY_TOKEN`, cùng
   `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`.

### Chạy thử local

```bash
python daily_notify.py
```

### Trên điện thoại

- Bật kênh thông báo của ntfy ở mức **High** để có rung và tiếng.
- Tắt tối ưu pin (battery optimisation) cho app ntfy, nếu không thông báo
  có thể bị trễ vài phút.


## Kiểm tra

```bash
pytest -q
python -m py_compile *.py
```

## Ghi chú vận hành

- `service-account.json`, `.env`, và mọi khóa Google không được commit.
- Nếu một crawler nguồn bị lỗi, `run_hour.py` không xóa dữ liệu Calendar hiện có của nguồn đó.
- Script không gửi thông báo; lịch được đọc trực tiếp từ Google Calendar.
