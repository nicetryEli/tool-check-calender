"""
auth_google_tasks.py - one-time OAuth setup for reading Google Tasks.

    python auth_google_tasks.py login    # opens your browser, prints refresh token
    python auth_google_tasks.py test     # verify the stored refresh token works

Google retired the out-of-band redirect (urn:ietf:wg:oauth:2.0:oob) in 2022,
so this uses the loopback flow instead: a temporary server on localhost catches
the redirect and captures the code. Because this must run on the machine where
your browser is, run it locally.

The refresh token does not expire, so this is a one-time step.
"""

import os
import sys

from dotenv import load_dotenv

SCOPES = ["https://www.googleapis.com/auth/tasks"]
LOOPBACK_PORT = 8765


def _client_config() -> dict:
    load_dotenv()
    client_id = (os.environ.get("GOOGLE_TASKS_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GOOGLE_TASKS_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        print(
            "Missing GOOGLE_TASKS_CLIENT_ID / GOOGLE_TASKS_CLIENT_SECRET in .env.\n"
            "Create an OAuth client of type 'Desktop app' in the Google Cloud console."
        )
        sys.exit(1)
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"http://localhost:{LOOPBACK_PORT}"],
        }
    }


def run_login() -> None:
    """Open the browser, capture the redirect on localhost, print the token."""
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from google_auth_oauthlib.flow import InstalledAppFlow
    from urllib.parse import parse_qs, urlparse

    # redirect_uri must be passed here, at construction. from_client_config ignores
    # the redirect_uris entry in the JSON, and without this the authorization URL
    # omits redirect_uri and Google rejects it.
    redirect_uri = f"http://localhost:{LOOPBACK_PORT}"
    flow = InstalledAppFlow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=redirect_uri
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )

    print("=" * 78, flush=True)
    print("AUTHORIZE THIS URL (the browser should also open automatically):", flush=True)
    print(auth_url, flush=True)
    print("=" * 78, flush=True)
    print("Waiting for the redirect back to localhost...", flush=True)

    captured: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            if "code" in query:
                captured["code"] = query["code"][0]
                body = b"<html><body><h3>Authorization received.</h3><p>You can close this tab.</p></body></html>"
                content_type = "text/html"
            else:
                error = (query.get("error") or ["unknown"])[0]
                captured["error"] = error
                body = f"<html><body><h3>Authorization failed.</h3><p>{error}</p></body></html>".encode()
                content_type = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            return

    server = HTTPServer(("localhost", LOOPBACK_PORT), Handler)
    try:
        webbrowser.open(auth_url)
        # The callback aborts this request, so serve until a code arrives.
        server.timeout = 1.0
        deadline = 300
        waited = 0.0
        while "code" not in captured and "error" not in captured and waited < deadline:
            server.handle_request()
            waited += 1.0
    finally:
        server.server_close()

    if "error" in captured:
        print(f"\nAuthorization failed: {captured['error']}", flush=True)
        sys.exit(1)
    if "code" not in captured:
        print("\nTimed out waiting for the redirect.", flush=True)
        sys.exit(1)

    flow.fetch_token(code=captured["code"])
    credentials = flow.credentials

    refresh_token = credentials.refresh_token
    if not refresh_token:
        print(
            "\nNo refresh_token returned. This usually means access was granted "
            "before without the consent prompt. Revoke it at "
            "https://myaccount.google.com/permissions and run this again."
        )
        sys.exit(1)

    print("\nAdd this to .env (and as a GitHub secret):\n")
    print(f"GOOGLE_TASKS_REFRESH_TOKEN={refresh_token}\n")
    print("Then verify with: python auth_google_tasks.py test")


def test_connection() -> None:
    """Verify the stored refresh token can read task lists."""
    import daily_notify

    load_dotenv()
    try:
        tasks = daily_notify.fetch_tasks()
    except Exception as exc:
        print(f"Failed: {exc}")
        sys.exit(1)

    pending = daily_notify.filter_incomplete_tasks(tasks)
    print(f"Connected. {len(tasks)} task(s) found, {len(pending)} not done yet.")
    for task in pending:
        print("  -", task.get("title"), "| due:", task.get("due") or "none")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "login"
    if command == "login":
        run_login()
    elif command == "test":
        test_connection()
    else:
        print("Usage: python auth_google_tasks.py [login|test]")
        sys.exit(1)

