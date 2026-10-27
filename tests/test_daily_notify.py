import base64
import datetime as dt
import unittest

from daily_notify import (
    build_message,
    encode_header_value,
    filter_incomplete_tasks,
    format_day_label,
    format_task_line,
    to_notification_row,
)


class HeaderEncodingTests(unittest.TestCase):
    def test_ascii_header_value_is_left_unchanged(self) -> None:
        self.assertEqual(encode_header_value("Today's schedule"), "Today's schedule")

    def test_non_ascii_header_value_is_encoded_as_rfc2047(self) -> None:
        # ntfy documents that non-ASCII header values render as "?" unless the
        # client encodes them as RFC 2047. Vietnamese titles must survive.
        encoded = encode_header_value("Kế hoạch hôm nay")

        self.assertTrue(encoded.startswith("=?UTF-8?B?"), encoded)
        self.assertTrue(encoded.endswith("?="), encoded)
        payload = encoded[len("=?UTF-8?B?") : -len("?=")]
        self.assertEqual(base64.b64decode(payload).decode("utf-8"), "Kế hoạch hôm nay")


class DayLabelTests(unittest.TestCase):
    def test_day_label_uses_vietnamese_weekday_name(self) -> None:
        self.assertEqual(format_day_label(dt.date(2026, 10, 13)), "Thứ 3, 13/10/2026")

    def test_sunday_is_labelled_chu_nhat(self) -> None:
        self.assertEqual(format_day_label(dt.date(2026, 10, 18)), "Chủ nhật, 18/10/2026")


class IncompleteTaskTests(unittest.TestCase):
    def test_completed_tasks_are_dropped(self) -> None:
        tasks = [
            {"title": "Nộp báo cáo", "status": "needsAction", "due": "2026-10-20T00:00:00.000Z"},
            {"title": "Đã xong", "status": "completed", "due": "2026-10-10T00:00:00.000Z"},
        ]

        self.assertEqual([t["title"] for t in filter_incomplete_tasks(tasks)], ["Nộp báo cáo"])

    def test_task_without_status_is_treated_as_incomplete(self) -> None:
        tasks = [{"title": "Không có status", "due": ""}]

        self.assertEqual(len(filter_incomplete_tasks(tasks)), 1)

    def test_blank_titles_are_dropped(self) -> None:
        self.assertEqual(filter_incomplete_tasks([{"title": "   ", "status": "needsAction"}]), [])


class TaskLineTests(unittest.TestCase):
    def test_overdue_task_is_labelled_as_late(self) -> None:
        line = format_task_line(
            {"title": "Nộp báo cáo", "status": "needsAction", "due": "2026-10-05T00:00:00.000Z"},
            dt.date(2026, 10, 13),
        )

        self.assertIn("Nộp báo cáo", line)
        self.assertIn("quá hạn", line)
        self.assertIn("05/10", line)

    def test_future_task_shows_plain_due_date(self) -> None:
        line = format_task_line(
            {"title": "Mua sách", "status": "needsAction", "due": "2026-10-20T00:00:00.000Z"},
            dt.date(2026, 10, 13),
        )

        self.assertIn("Mua sách", line)
        self.assertIn("20/10", line)
        self.assertNotIn("quá hạn", line)

    def test_task_without_due_date_says_so(self) -> None:
        line = format_task_line({"title": "Someday", "status": "needsAction"}, dt.date(2026, 10, 13))

        self.assertIn("Someday", line)
        self.assertIn("chưa đặt hạn", line)


class BuildMessageTests(unittest.TestCase):
    def test_message_lists_tasks_and_todays_events(self) -> None:
        message = build_message(
            dt.date(2026, 10, 13),
            [{"start_time": "06:50", "end_time": "09:20", "title": "Lập trình web", "location": "B302"}],
            [{"title": "Nộp báo cáo", "status": "needsAction", "due": "2026-10-20T00:00:00.000Z"}],
        )

        self.assertIn("VIỆC CHƯA LÀM", message)
        self.assertIn("Nộp báo cáo", message)
        self.assertIn("HÔM NAY", message)
        self.assertIn("Thứ 3, 13/10/2026", message)
        self.assertIn("06:50 - 09:20", message)
        self.assertIn("Lập trình web", message)
        self.assertIn("B302", message)

    def test_overdue_tasks_are_listed_before_future_ones(self) -> None:
        message = build_message(
            dt.date(2026, 10, 13),
            [],
            [
                {"title": "Task moi", "status": "needsAction", "due": "2026-10-25T00:00:00.000Z"},
                {"title": "Task cu", "status": "needsAction", "due": "2026-09-01T00:00:00.000Z"},
            ],
        )

        self.assertLess(message.index("Task cu"), message.index("Task moi"))

    def test_no_tasks_still_renders_the_sections(self) -> None:
        message = build_message(dt.date(2026, 10, 13), [], [])

        self.assertIn("VIỆC CHƯA LÀM", message)
        self.assertIn("HÔM NAY", message)
        self.assertNotIn("chưa đặt hạn", message)

    def test_todays_rows_are_sorted_by_start_time(self) -> None:
        message = build_message(
            dt.date(2026, 10, 13),
            [
                {"start_time": "12:45", "end_time": "14:35", "title": "Buoi chieu", "location": "A1"},
                {"start_time": "06:50", "end_time": "08:20", "title": "Buoi sang", "location": "B2"},
            ],
            [],
        )

        self.assertLess(message.index("Buoi sang"), message.index("Buoi chieu"))

    def test_row_without_location_omits_the_separator(self) -> None:
        message = build_message(
            dt.date(2026, 10, 13),
            [{"start_time": "06:50", "end_time": "08:20", "title": "Khong ro phong", "location": ""}],
            [],
        )

        self.assertIn("Khong ro phong", message)
        self.assertNotIn("@", message)

    def test_all_tasks_done_reports_that_clearly(self) -> None:
        message = build_message(dt.date(2026, 10, 13), [], [])

        self.assertIn("Không có việc nào", message)


class TaggedRowConversionTests(unittest.TestCase):
    """fetch_events_from_calendar drops [DEADLINE] events, so they are fetched
    separately and converted here."""

    def test_tagged_row_converts_iso_start_to_local_clock_time(self) -> None:
        result = to_notification_row(
            {
                "title": "[DEADLINE] Lab 5",
                "start": "2026-10-16T23:59:00+07:00",
                "end": "2026-10-16T23:59:00+07:00",
                "location": "",
                "notes": "",
            },
            "Asia/Ho_Chi_Minh",
        )

        self.assertIsNotNone(result)
        event_date, row = result
        self.assertEqual(event_date, dt.date(2026, 10, 16))
        self.assertEqual(row["start_time"], "23:59")
        self.assertEqual(row["title"], "[DEADLINE] Lab 5")

    def test_tagged_row_start_is_rendered_in_app_timezone(self) -> None:
        # 18:00 UTC is 01:00 the next day in Vietnam.
        result = to_notification_row(
            {"title": "X", "start": "2026-10-16T18:00:00Z", "end": "", "location": "", "notes": ""},
            "Asia/Ho_Chi_Minh",
        )

        self.assertIsNotNone(result)
        event_date, row = result
        self.assertEqual(event_date, dt.date(2026, 10, 17))
        self.assertEqual(row["start_time"], "01:00")

    def test_all_day_row_has_no_clock_time(self) -> None:
        result = to_notification_row(
            {"title": "All day", "start": "2026-10-16", "end": "2026-10-17", "location": "", "notes": ""},
            "Asia/Ho_Chi_Minh",
        )

        self.assertIsNotNone(result)
        event_date, row = result
        self.assertEqual(event_date, dt.date(2026, 10, 16))
        self.assertEqual(row["start_time"], "")
        self.assertEqual(row["end_time"], "")

    def test_unparseable_start_is_discarded(self) -> None:
        self.assertIsNone(
            to_notification_row(
                {"title": "Bad", "start": "not-a-date", "end": "", "location": "", "notes": ""},
                "Asia/Ho_Chi_Minh",
            )
        )


if __name__ == "__main__":
    unittest.main()
