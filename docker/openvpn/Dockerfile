FROM python:3.14-alpine

WORKDIR /app

RUN apk add --no-cache openvpn \
    && pip install --no-cache-dir "pydantic>=2.13,<3"

COPY browser_vpn_runtime /app/browser_vpn_runtime

ENTRYPOINT ["python", "-m", "browser_vpn_runtime.openvpn_sidecar"]
