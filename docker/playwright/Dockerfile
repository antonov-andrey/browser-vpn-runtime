ARG PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright/python:latest
FROM ${PLAYWRIGHT_IMAGE}

WORKDIR /app

COPY pyproject.toml requirements.txt ./
COPY browser_vpn_runtime ./browser_vpn_runtime

RUN python -m pip install --no-cache-dir .

ENTRYPOINT ["browser-vpn-runtime-readiness"]
