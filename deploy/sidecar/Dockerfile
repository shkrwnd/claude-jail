FROM alpine:3.20
RUN apk add --no-cache tinyproxy dnsmasq socat
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY allowlist /etc/tinyproxy/allowlist
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 53 8377 8888
ENTRYPOINT ["/entrypoint.sh"]
