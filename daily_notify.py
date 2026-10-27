"""
daily_notify.py - Daily morning notification via ntfy.

Execution flow:
    1. Load environment variables from a local .env file (if present).
    2. Read unfinished tasks from Google Tasks (OAuth).
    3. Read today's schedule from Google Calendar.
    4. Publish one ntfy notification combining both.

Data collection and Calendar synchronization are handled separately by run_hour.py.
This script only reads existing data, so it does not crawl the TDTU portal.

Google Tasks cannot be read with a service account (task lists are personal and
cannot be shared), so it uses the user's own OAuth credentials instead.
"""

import base64
import datetime as dt
import logging
import os
import sys

import requests
from zoneinfo import ZoneInfo

from time_utils import local_today

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

NTFY_BASE_URL = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
DEFAULT_PRIORITY = "high"
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
VIETNAMESE_WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]


def _load_dotenv() -> None:
    """Load .env file into os.environ when running locally."""
    try:
        from dotenv import load_dotenv  # type: ignore[import]

        if load_dotenv():
            logger.info(".env file loaded successfully.")
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Header encoding
# ---------------------------------------------------------------------------

def encode_header_value(text: str) -> str:
    """Return an HTTP header value that survives non-ASCII characters.

    ntfy documents that non-ASCII header values render as "?" in some HTTP
    clients. RFC 2047 encoded-word form is the documented workaround.
    """
    value = str(text)
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return f"=?UTF-8?B?{encoded}?="
    return value


# ---------------------------------------------------------------------------
# Google Tasks
# ---------------------------------------------------------------------------

def filter_incomplete_tasks(tasks: list[dict]) -> list[dict]:
    """Return only tasks that are not completed and have a visible title."""
    result = []
    for task in tasks:
        status = str(task.get("status") or "needsAction").strip().lower()
        title = str(task.get("title") or "").strip()
        if status == "completed" or not title:
            continue
        result.append(task)
    return result


def _task_due_date(task: dict, tz: ZoneInfo) -> dt.date | None:
    """Parse a task's RFC 3339 due timestamp into a local date."""
    raw = str(task.get("due") or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return parsed.date()


def format_task_line(task: dict, today: dt.date, tz: ZoneInfo | None = None) -> str:
    """Format one task as a notification line, marking it late when overdue."""
    title = str(task.get("title") or "").strip()
    due = _task_due_date(task, tz or ZoneInfo("Asia/Ho_Chi_Minh"))

    if due is None:
        return f"• {title} (chưa đặt hạn)"
    if due < today:
        return f"• {title} (quá hạn {due.strftime('%d/%m')})"
    return f"• {title} ({due.strftime('%d/%m')})"


def fetch_tasks() -> list[dict]:
    """
    Fetch every task from every Google Tasks list using the user's OAuth token.

    Raises
    ------
    RuntimeError
        If the OAuth credentials are missing or the API call fails.
    """
    client_id = (os.environ.get("GOOGLE_TASKS_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GOOGLE_TASKS_CLIENT_SECRET") or "").strip()
    refresh_token = (os.environ.get("GOOGLE_TASKS_REFRESH_TOKEN") or "").strip()
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "Google Tasks credentials missing. Set GOOGLE_TASKS_CLIENT_ID, "
            "GOOGLE_TASKS_CLIENT_SECRET and GOOGLE_TASKS_REFRESH_TOKEN."
        )

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[TASKS_SCOPE],
    )
    service = build("tasks", "v1", credentials=credentials, cache_discovery=False)

    tasks: list[dict] = []
    tasklists = service.tasklists().list().execute()
    for tasklist in tasklists.get("items", []):
        list_id = tasklist.get("id")
        if not list_id:
            continue
        page_token = None
        while True:
            resp = (
                service.tasks()
                .list(
                    tasklist=list_id,
                    showCompleted=True,
                    showHidden=True,
                    maxResults=100,
                    pageToken=page_token,
                )
                .execute()
            )
            tasks.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    logger.info("Fetched %d task(s) from Google Tasks.", len(tasks))
    return tasks


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------

def format_day_label(target_date: dt.date) -> str:
    """Format a date as a Vietnamese day label, e.g. 'Thứ 3, 13/10/2026'."""
    return f"{VIETNAMESE_WEEKDAYS[target_date.weekday()]}, {target_date.strftime('%d/%m/%Y')}"


def _format_row(row: dict) -> str:
    """Format a single calendar row as one line of the notification body."""
    start = str(row.get("start_time") or "").strip()
    end = str(row.get("end_time") or "").strip()
    title = str(row.get("title") or "").strip() or "N/A"
    location = str(row.get("location") or "").strip()

    if start and end:
        time_range = f"{start} - {end}"
    else:
        time_range = start or end

    line = f"{time_range}  {title}".strip()
    if location:
        line += f"  @ {location}"
    return line


def _sort_tasks(tasks: list[dict], today: dt.date, tz: ZoneInfo) -> list[dict]:
    """Sort by due date oldest first, pushing undated tasks to the end."""
    def key(task: dict) -> tuple[int, str]:
        due = _task_due_date(task, tz)
        if due is None:
            return (1, "")
        return (0, due.isoformat())

    return sorted(tasks, key=key)


def build_message(today: dt.date, today_rows: list[dict], tasks: list[dict], tz: ZoneInfo | None = None) -> str:
    """Build the notification body: unfinished tasks, then today's events."""
    tz = tz or ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Ho_Chi_Minh"))

    pending = _sort_tasks(filter_incomplete_tasks(tasks), today, tz)

    if pending:
        task_block = [f"VIỆC CHƯA LÀM ({len(pending)})", ""]
        task_block.extend(format_task_line(task, today, tz) for task in pending)
    else:
        task_block = ["VIỆC CHƯA LÀM", "", "Không có việc nào chưa làm. 🎉"]

    event_block = [f"HÔM NAY – {format_day_label(today)}", ""]
    if today_rows:
        ordered = sorted(today_rows, key=lambda r: str(r.get("start_time") or ""))
        event_block.extend(_format_row(row) for row in ordered)
    else:
        event_block.append("Không có lớp nào.")

    return "\n\n".join(["\n".join(task_block), "\n".join(event_block), "_Chúc bạn học tốt!_"])


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------

def to_notification_row(raw: dict, timezone: str) -> tuple[dt.date, dict] | None:
    """
    Convert a ``fetch_tagged_calendar_events`` row into a notification row.

    Returns the event's local date together with the row, or None when the
    start value cannot be parsed.
    """
    tz = ZoneInfo(timezone)
    raw_start = str(raw.get("start") or "").strip()
    if not raw_start:
        return None

    if len(raw_start) == 10:
        try:
            event_date = dt.date.fromisoformat(raw_start)
        except ValueError:
            return None
        start_time = ""
        end_time = ""
    else:
        try:
            start = dt.datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        except ValueError:
            return None
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        else:
            start = start.astimezone(tz)
        event_date = start.date()
        start_time = start.strftime("%H:%M")

        end_time = ""
        raw_end = str(raw.get("end") or "").strip()
        if len(raw_end) > 10:
            try:
                end = dt.datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
            except ValueError:
                end = None
            if end is not None:
                if end.tzinfo is None:
                    end = end.replace(tzinfo=tz)
                else:
                    end = end.astimezone(tz)
                if end.date() == event_date:
                    end_time = end.strftime("%H:%M")

    row = {
        "start_time": start_time,
        "end_time": end_time,
        "title": str(raw.get("title") or "").strip(),
        "location": str(raw.get("location") or "").strip(),
    }
    return event_date, row


def _calendar_day_url(target_date: dt.date) -> str:
    """Deep link that opens Google Calendar on the day view for *target_date*."""
    return (
        "https://calendar.google.com/calendar/u/0/r/day/"
        f"{target_date.year}/{target_date.month}/{target_date.day}"
    )


def _collect_day(target_date: dt.date) -> list[dict]:
    """Return class, appointment, exam and deadline rows for a single calendar day."""
    from calendar_sync import (
        SYNC_SOURCE_DEADLINE,
        fetch_events_from_calendar,
        fetch_tagged_calendar_events,
    )

    classes, appointments, exams = fetch_events_from_calendar(target_date=target_date)

    rows: list[dict] = []
    for source in (classes, appointments):
        rows.extend(
            {
                "start_time": r.get("start_time", ""),
                "end_time": r.get("end_time", ""),
                "title": r.get("title", ""),
                "location": r.get("location", ""),
            }
            for r in source
        )

    day = target_date.isoformat()
    rows.extend(
        {
            "start_time": r.get("start_time", ""),
            "end_time": r.get("end_time", ""),
            "title": r.get("title", ""),
            "location": r.get("location", ""),
        }
        for r in exams
        if str(r.get("appointment_date", "")) == day
    )

    # fetch_events_from_calendar has no branch for [DEADLINE] events, so they are
    # fetched separately here rather than being silently dropped.
    timezone = os.environ.get("APP_TIMEZONE", "Asia/Ho_Chi_Minh")
    for raw in fetch_tagged_calendar_events(SYNC_SOURCE_DEADLINE, target_date=target_date, days_ahead=1):
        converted = to_notification_row(raw, timezone)
        if converted is not None and converted[0] == target_date:
            rows.append(converted[1])

    return rows


# ---------------------------------------------------------------------------
# ntfy publishing
# ---------------------------------------------------------------------------

def send_ntfy(topic: str, token: str, title: str, message: str, priority: str = DEFAULT_PRIORITY, click_url: str = "") -> None:
    """
    Publish *message* to an ntfy topic.

    Raises
    ------
    RuntimeError
        On non-2xx HTTP status or a ntfy API error.
    """
    url = f"{NTFY_BASE_URL}/{topic}"
    headers = {
        "Title": encode_header_value(title),
        "Priority": priority,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if click_url:
        headers["Click"] = click_url

    logger.info("Publishing ntfy notification to %s.", url)
    try:
        response = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"ntfy request failed: {exc}") from exc

    if not response.ok:
        raise RuntimeError(f"ntfy API error {response.status_code}: {response.text}")

    logger.info("ntfy notification published successfully.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Send unfinished tasks and today's schedule as one ntfy notification."""
    _load_dotenv()

    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    if not topic:
        logger.error("NTFY_TOPIC is not set; cannot publish notification.")
        sys.exit(1)

    today = local_today()
    tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Ho_Chi_Minh"))

    try:
        tasks = fetch_tasks()
    except Exception as exc:
        logger.warning("Could not read Google Tasks (%s); continuing without tasks.", exc)
        tasks = []

    try:
        today_rows = _collect_day(today)
    except Exception as exc:
        logger.exception("Failed to read schedule from Google Calendar: %s", exc)
        sys.exit(1)

    message = build_message(today, today_rows, tasks, tz)
    logger.info("Notification body:\n%s", message)

    try:
        send_ntfy(
            topic=topic,
            token=(os.environ.get("NTFY_TOKEN") or "").strip(),
            title="Kế hoạch hôm nay",
            message=message,
            click_url=_calendar_day_url(today),
        )
    except Exception as exc:
        logger.exception("Failed to publish ntfy notification: %s", exc)
        sys.exit(1)

    logger.info("=== Daily notification complete. ===")


if __name__ == "__main__":
    main()
