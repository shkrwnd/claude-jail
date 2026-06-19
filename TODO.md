# TODO

## Egress Enforcement
The `deploy/egress-config.json` defines allowed domains but nothing enforces it.
Need to decide and implement one of:
- **iptables** (`init-firewall.sh`) — resolves allowed domains to IPs at startup, blocks all other outbound traffic. Requires `NET_ADMIN` + `NET_RAW` caps. Simpler but IP-based not domain-based.
- **Egress proxy** — all traffic routes through a proxy that checks domain names in HTTP/TLS SNI. Airtight domain-level enforcement but more complex to build.

Clarify first: is there an existing proxy implementation, or does this need to be built from scratch?
