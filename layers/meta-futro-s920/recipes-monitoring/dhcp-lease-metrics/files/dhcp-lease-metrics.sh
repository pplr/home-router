#!/bin/sh
# Render systemd-networkd's DHCP server leases as node_exporter textfile
# metrics, one series per lease:
#
#   router_dhcp_lease_expiry_timestamp_seconds{bridge,address,mac,hostname} <unix>
#
# networkd persists each bridge's leases as JSON in LEASE_DIR/<bridge>
# (Address as a byte array, HardwareAddress zero-padded to 16 bytes,
# ExpirationRealtimeUSec in µs). The value is the absolute expiry, so this
# only has to run when the files change (dhcp-lease-metrics.path), not on a
# timer: dashboards compute the time left and hide expired leases with
# `> time()`.
set -eu

LEASE_DIR=/var/lib/systemd/network/dhcp-server-lease
OUT_DIR=/run/node-exporter/textfile
OUT="$OUT_DIR/dhcp-leases.prom"
# node_exporter only reads *.prom, so the temporary name must not end in it.
TMP="$OUT.tmp"

# shellcheck disable=SC2016  # $bridge is a jq variable
JQ_PROGRAM='
def esc: gsub("\\\\"; "\\\\") | gsub("\""; "\\\"") | gsub("\n"; "\\n");
def nibble: . as $n | "0123456789abcdef" | .[$n:$n + 1];
def byte: ((. / 16 | floor) | nibble) + (. % 16 | nibble);
(.Leases // [])[]
| "router_dhcp_lease_expiry_timestamp_seconds{"
  + "bridge=\"\($bridge)\","
  + "address=\"\(.Address | map(tostring) | join("."))\","
  + "mac=\"\(.HardwareAddress[0:.HardwareAddressLength] | map(byte) | join(":"))\","
  + "hostname=\"\(.Hostname // "" | esc)\"} "
  + "\(.ExpirationRealtimeUSec / 1000000 | floor)"
'

{
    echo '# HELP router_dhcp_lease_expiry_timestamp_seconds Expiry of a lease handed out by the systemd-networkd DHCP server.'
    echo '# TYPE router_dhcp_lease_expiry_timestamp_seconds gauge'
    for f in "$LEASE_DIR"/*; do
        [ -f "$f" ] || continue
        jq -r --arg bridge "${f##*/}" "$JQ_PROGRAM" "$f"
    done
} > "$TMP"
mv -f "$TMP" "$OUT"
