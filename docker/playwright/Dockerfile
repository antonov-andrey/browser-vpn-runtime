ARG PLAYWRIGHT_IMAGE=python:3.14-bookworm
FROM ${PLAYWRIGHT_IMAGE}

ENV NPM_CONFIG_UPDATE_NOTIFIER=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg tini xauth xvfb \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g npm@11.18.0 @playwright/mcp@0.0.77 playwright@1.61.1 \
    && playwright install --with-deps chrome \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY browser_vpn_runtime ./browser_vpn_runtime

RUN python -m pip install --root-user-action=ignore --no-cache-dir ".[browser]" \
    && groupadd --gid 1000 browser \
    && useradd --uid 1000 --gid browser --create-home --shell /usr/sbin/nologin browser \
    && mkdir -p /output /runtime /runtime-profile \
    && chown -R browser:browser /app /output /runtime /runtime-profile

USER root

ENTRYPOINT ["/usr/bin/tini", "--", "browser-vpn-runtime-container-entrypoint"]
