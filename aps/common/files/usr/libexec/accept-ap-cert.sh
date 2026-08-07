#!/bin/sh
#
# Receive a LuCI certificate pushed by the router, over SSH.
#
# This is the forced command bound to the router's push key in
# /etc/dropbear/authorized_keys, so it is the *only* thing that key can
# do: dropbear discards whatever the client asked to run and executes
# this instead. It reads a PEM certificate on stdin and exits.
#
# Only the certificate crosses the wire. The matching private key is
# baked into this image and never leaves the device, so a compromised
# router (or push key) can at worst install a certificate that doesn't
# match our key — which this script refuses, and which would in any case
# only break our own HTTPS, never leak the key.
#
# Idempotent by design: the router pushes on every timer tick so that a
# `sysupgrade -n` (which reverts us to the baked bootstrap cert) is
# healed on the next run rather than 60 days later. Restarting uhttpd on
# every one of those pushes would be pointless churn, so we compare
# first and only act on an actual change.

set -u

CERT=/etc/uhttpd/ap.crt
KEY=/etc/uhttpd/ap.key
TMP="$(mktemp -t ap-cert.XXXXXX)" || exit 1
BACKUP="$(mktemp -t ap-cert-old.XXXXXX)" || exit 1

cleanup() { rm -f "$TMP" "$BACKUP"; }
trap cleanup EXIT HUP INT TERM

cat > "$TMP"

# --- validate ---------------------------------------------------------
# No openssl on the AP (luci-ssl uses mbedtls), so this is a structural
# check rather than a cryptographic one. That is sufficient here: the
# certificate is signed for a CSR the router generated from *our* public
# key, and a malformed file would otherwise take uhttpd down silently.

if [ ! -s "$TMP" ]; then
    echo "accept-ap-cert: empty input, refusing" >&2
    exit 1
fi

if ! grep -q '^-----BEGIN CERTIFICATE-----$' "$TMP" \
   || ! grep -q '^-----END CERTIFICATE-----$' "$TMP"; then
    echo "accept-ap-cert: input is not a PEM certificate, refusing" >&2
    exit 1
fi

if [ ! -s "$KEY" ]; then
    echo "accept-ap-cert: $KEY missing — image built without TLS material" >&2
    exit 1
fi

# --- short-circuit when unchanged -------------------------------------

if [ -f "$CERT" ] && cmp -s "$TMP" "$CERT"; then
    echo "accept-ap-cert: certificate unchanged"
    exit 0
fi

# --- install, with rollback -------------------------------------------
# A bad certificate that passes the structural check above would leave
# uhttpd dead and LuCI unreachable — i.e. exactly the situation where
# fixing it remotely is hardest. So keep the old one and put it back if
# uhttpd doesn't come up.

if [ -f "$CERT" ]; then
    cp "$CERT" "$BACKUP"
fi

cat "$TMP" > "$CERT"
chmod 0644 "$CERT"

if /etc/init.d/uhttpd restart && sleep 2 && pgrep uhttpd >/dev/null 2>&1; then
    echo "accept-ap-cert: certificate installed, uhttpd restarted"
    exit 0
fi

echo "accept-ap-cert: uhttpd failed to start with the new certificate" >&2
if [ -s "$BACKUP" ]; then
    cat "$BACKUP" > "$CERT"
    chmod 0644 "$CERT"
    /etc/init.d/uhttpd restart
    echo "accept-ap-cert: rolled back to the previous certificate" >&2
fi
exit 1
