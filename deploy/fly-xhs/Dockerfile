FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 XHS_BROWSER_ROOT=/data/xhs
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg xvfb xauth gosu fonts-noto-cjk coreutils libatomic1 && \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg && \
    echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && apt-get install -y --no-install-recommends google-chrome-stable && \
    rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home browser
WORKDIR /app
COPY deploy/fly-xhs/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY aion-chat/xhs_browser_service.py aion-chat/xhs_fly_service.py aion-chat/xhs_lite_worker.js aion-chat/xhs_lite_worker_runner.mjs /app/
COPY deploy/fly-xhs/entrypoint.sh /usr/local/bin/xhs-entrypoint
RUN chmod 755 /usr/local/bin/xhs-entrypoint
ENTRYPOINT ["/usr/local/bin/xhs-entrypoint"]
