"""AWS Lambda entrypoint — wraps book_class.main() for a container-image
Lambda function triggered by an EventBridge Scheduler schedule.

Everything book_class.py already logs with print()/log() lands in
CloudWatch Logs automatically. On a real failure, this also files a
GitHub Issue with the error details (best-effort — never lets a problem
filing the issue mask or replace the real error), so a failed run is easy
to find and fix without digging through CloudWatch first.
"""
import json
import os
import traceback
import urllib.error
import urllib.request
from datetime import datetime

import book_class

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "TianweiZhou/pilates-booker")


def _file_github_issue(title: str, body: str) -> None:
    if not GITHUB_TOKEN:
        print("GITHUB_TOKEN not set — skipping auto-filed GitHub issue.")
        return
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/issues",
            data=json.dumps({
                "title": title,
                "body": body,
                "labels": ["auto-filed", "lambda-failure"],
            }).encode(),
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "pilates-booker-lambda",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            url = json.loads(resp.read()).get("html_url")
            print(f"Filed GitHub issue ({resp.status}): {url}")
    except Exception as e:
        detail = e.read().decode() if isinstance(e, urllib.error.HTTPError) else str(e)
        print(f"Could not file GitHub issue: {detail}")


def _issue_body(detail: str, request_id: str) -> str:
    when = datetime.now(book_class.TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    return (
        f"**When:** {when}\n"
        f"**Request ID:** `{request_id}`\n\n"
        f"```\n{detail}\n```\n\n"
        f"Check CloudWatch Logs (`/aws/lambda/pilates-booker`) for this "
        f"request ID for the full DIAG output — page URL, title, and "
        f"visible text at the moment of failure."
    )


def handler(event, context):
    request_id = getattr(context, "aws_request_id", "unknown")
    try:
        book_class.main()
        return {"ok": True}
    except SystemExit as e:
        # book_class.py calls sys.exit(0) for boring, successful outcomes
        # (e.g. "already booked", "too early to start"). Only a non-zero
        # exit code should count as a real failure.
        code = e.code or 0
        if code != 0:
            msg = f"book_class.main() exited with code {code}"
            _file_github_issue(
                f"Pilates booking failed — {datetime.now(book_class.TZ):%Y-%m-%d}",
                _issue_body(msg, request_id),
            )
            raise RuntimeError(msg)
        return {"ok": True, "note": "exited early — see logs for the reason"}
    except Exception:
        tb = traceback.format_exc()
        traceback.print_exc()
        _file_github_issue(
            f"Pilates booking failed — {datetime.now(book_class.TZ):%Y-%m-%d}",
            _issue_body(tb, request_id),
        )
        raise
