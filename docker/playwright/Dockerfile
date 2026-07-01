ARG PLAYWRIGHT_IMAGE=python:3.14-bookworm
FROM ${PLAYWRIGHT_IMAGE}

ENV NPM_CONFIG_UPDATE_NOTIFIER=false

WORKDIR /app

COPY pyproject.toml requirements.txt ./
COPY browser_vpn_runtime ./browser_vpn_runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg xauth xvfb \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g npm@11.18.0 @openai/codex@0.142.4 @playwright/mcp@0.0.77 playwright@1.61.1 \
    && python -m pip install --root-user-action=ignore --no-cache-dir . \
    && playwright install --with-deps chrome \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["browser-vpn-runtime-readiness"]
