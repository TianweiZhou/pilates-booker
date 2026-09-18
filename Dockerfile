# Lambda container image for the pilates auto-booker.
#
# Base: Playwright's own Python image — Ubuntu with Chromium/Firefox/WebKit
# and every system library they need already installed. We add the AWS
# Lambda Runtime Interface Client (awslambdaric) on top so the same image
# can run as a container-image Lambda function. Pin this tag to match
# requirements.txt's playwright version exactly.
FROM mcr.microsoft.com/playwright/python:v1.62.0-jammy

WORKDIR /var/task

COPY requirements-lambda.txt .
RUN pip install --no-cache-dir -r requirements-lambda.txt

COPY book_class.py lambda_handler.py ./

# /var/task is read-only at runtime; screenshots must go to /tmp instead.
ENV SCREENSHOT_DIR=/tmp/screenshots
# Chromium wants a writable home directory for its profile/cache — /tmp is
# the only writable filesystem in a Lambda container.
ENV HOME=/tmp
ENV XDG_CONFIG_HOME=/tmp/.config
ENV XDG_CACHE_HOME=/tmp/.cache

ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["lambda_handler.handler"]
