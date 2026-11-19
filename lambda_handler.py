"""AWS Lambda entrypoint — wraps book_class.main() for a container-image
Lambda function triggered by an EventBridge Scheduler schedule.

Everything book_class.py already logs with print()/log() lands in
CloudWatch Logs automatically. After each run, the run's screenshots are
uploaded to S3 (SCREENSHOT_BUCKET), since the Lambda's /tmp is discarded.
On a real failure, this also files a GitHub Issue with the error details
and the screenshots' S3 location (best-effort — never lets a problem
filing the issue or uploading mask or replace the real error).
"""
import json
import os
import threading
import traceback
import urllib.error
import urllib.request
from datetime import datetime

import book_class

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "TianweiZhou/pilates-booker")
SCREENSHOT_BUCKET = os.environ.get("SCREENSHOT_BUCKET", "")


def _clear_screenshots() -> None:
    # A warm Lambda reuses /tmp, so drop the previous run's screenshots.
    for f in book_class.SHOTS.glob("*.png"):
        f.unlink(missing_ok=True)


def _upload_screenshots(request_id: str) -> str:
    """Upload this run's screenshots; return a link to them, or ''."""
    files = sorted(book_class.SHOTS.glob("*.png"))
    if not SCREENSHOT_BUCKET or not files:
        return ""
    try:
        import boto3
        prefix = f"{datetime.now(book_class.TZ):%Y-%m-%d}/{request_id}/"
        s3 = boto3.client("s3")
        for f in files:
            s3.upload_file(str(f), SCREENSHOT_BUCKET, prefix + f.name,
                           ExtraArgs={"ContentType": "image/png"})
        url = (f"https://s3.console.aws.amazon.com/s3/buckets/"
               f"{SCREENSHOT_BUCKET}?region=us-east-1&prefix={prefix}")
        print(f"Uploaded {len(files)} screenshot(s) to "
              f"s3://{SCREENSHOT_BUCKET}/{prefix} — {url}")
        return url
    except Exception as e:
        print(f"Could not upload screenshots: {e}")
        return ""


def _github(path: str, payload: dict | None = None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "pilates-booker-lambda",
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _file_github_issue(title: str, body: str) -> None:
    """Open an issue, or comment on today's if one is already open — the
    primary run, the backup run, and Lambda's own async retries would
    otherwise each file a duplicate for the same failure."""
    if not GITHUB_TOKEN:
        print("GITHUB_TOKEN not set — skipping auto-filed GitHub issue.")
        return
    try:
        open_issues = _github("/issues?state=open&labels=lambda-failure&per_page=50")
        existing = next((i for i in open_issues if i["title"] == title), None)
        if existing:
            _github(f"/issues/{existing['number']}/comments", {"body": body})
            print(f"Commented on existing GitHub issue: {existing['html_url']}")
            return
        issue = _github("/issues", {
            "title": title,
            "body": body,
            "labels": ["auto-filed", "lambda-failure"],
        })
        print(f"Filed GitHub issue: {issue.get('html_url')}")
    except Exception as e:
        detail = e.read().decode() if isinstance(e, urllib.error.HTTPError) else str(e)
        print(f"Could not file GitHub issue: {detail}")


def _issue_body(detail: str, request_id: str, shots_url: str = "") -> str:
    when = datetime.now(book_class.TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    shots = (f"**Screenshots:** {shots_url} (kept for 7 days)\n"
             if shots_url else "")
    return (
        f"**When:** {when}\n"
        f"**Request ID:** `{request_id}`\n"
        f"{shots}\n"
        f"```\n{detail}\n```\n\n"
        f"Check CloudWatch Logs (`/aws/lambda/pilates-booker`) for this "
        f"request ID for the full DIAG output — page URL, title, and "
        f"visible text at the moment of failure."
    )


def _issue_title() -> str:
    return f"Pilates booking failed — {datetime.now(book_class.TZ):%Y-%m-%d}"


def _start_timeout_watchdog(context, request_id: str):
    """File an issue shortly before Lambda's hard timeout. A timeout kills
    the process outright, so the except blocks below never get to run."""
    get_remaining = getattr(context, "get_remaining_time_in_millis", None)
    if get_remaining is None:
        return None
    seconds_left = get_remaining() / 1000 - 30
    if seconds_left <= 0:
        return None
    def on_timeout():
        _file_github_issue(_issue_title(), _issue_body(
            "The run was still going 30s before the Lambda timeout and is "
            "about to be killed — no booking was made.",
            request_id, _upload_screenshots(request_id)))

    timer = threading.Timer(seconds_left, on_timeout)
    timer.daemon = True
    timer.start()
    return timer


def handler(event, context):
    request_id = getattr(context, "aws_request_id", "unknown")
    _clear_screenshots()
    watchdog = _start_timeout_watchdog(context, request_id)
    try:
        return _run(request_id)
    finally:
        if watchdog:
            watchdog.cancel()


def _run(request_id: str):
    try:
        book_class.main()
        _upload_screenshots(request_id)
        return {"ok": True}
    except SystemExit as e:
        # book_class.py calls sys.exit(0) for boring, successful outcomes
        # (e.g. "already booked", "too early to start"). Only a non-zero
        # exit code should count as a real failure.
        code = e.code or 0
        if code != 0:
            msg = f"book_class.main() exited with code {code}"
            _file_github_issue(_issue_title(), _issue_body(
                msg, request_id, _upload_screenshots(request_id)))
            raise RuntimeError(msg)
        _upload_screenshots(request_id)
        return {"ok": True, "note": "exited early — see logs for the reason"}
    except Exception:
        tb = traceback.format_exc()
        traceback.print_exc()
        _file_github_issue(_issue_title(), _issue_body(
            tb, request_id, _upload_screenshots(request_id)))
        raise
