#!/bin/sh
# Sidecar: runs tinyproxy (egress filter), dnsmasq (DNS), and socat (exec relay)
# all in one container to keep the compose file simple.

set -e

# DNS forwarder — resolves external names for the internal jail network
dnsmasq --no-daemon --no-resolv --server=8.8.8.8 --server=8.8.4.4 \
  --listen-address=0.0.0.0 --port=53 &

# Exec relay — forwards port 8377 to the host execution server
socat TCP-LISTEN:8377,fork,reuseaddr TCP:host.docker.internal:8377 &

# Egress proxy — domain-filtered forward proxy (foreground)
exec tinyproxy -d
