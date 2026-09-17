"""AWS Lambda entrypoint — wraps book_class.main() for a container-image
Lambda function triggered by an EventBridge Scheduler schedule.

Everything book_class.py already logs with print()/log() lands in
CloudWatch Logs automatically; there is nothing extra to wire up here.
"""
import traceback

import book_class


def handler(event, context):
    try:
        book_class.main()
        return {"ok": True}
    except SystemExit as e:
        # book_class.py calls sys.exit(0) for boring, successful outcomes
        # (e.g. "already booked", "too early to start"). Only a non-zero
        # exit code should count as a real failure.
        code = e.code or 0
        if code != 0:
            raise RuntimeError(f"book_class.main() exited with code {code}")
        return {"ok": True, "note": "exited early — see logs for the reason"}
    except Exception:
        # Print the full traceback explicitly (Lambda sometimes truncates
        # it), then re-raise so this invocation is recorded as a Lambda
        # error and shows up in CloudWatch metrics / alarms.
        traceback.print_exc()
        raise
