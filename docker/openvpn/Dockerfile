FROM python:3.14-alpine

WORKDIR /app

COPY pyproject.toml ./
COPY browser_vpn_runtime ./browser_vpn_runtime

RUN apk add --no-cache dante-server iptables openvpn supervisor \
    && adduser -D -H -s /sbin/nologin vpnproxy \
    && pip install --no-cache-dir .

ENTRYPOINT ["browser-vpn-runtime-vpn-egress"]
