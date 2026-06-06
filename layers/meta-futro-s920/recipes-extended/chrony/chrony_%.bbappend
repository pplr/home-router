FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

# Enable IPv6 so chrony can serve the LAN's GUA (2a01:e0a:97f:5432::1).
# The upstream chrony recipe leaves PACKAGECONFIG empty by default, which
# resolves to `--disable-ipv6` and would make chrony refuse to bind the v6
# addresses listed in our chrony.conf.
PACKAGECONFIG:append = " ipv6"

# Our chrony.conf (resolved via FILESEXTRAPATHS) replaces the upstream one
# in the SRC_URI fetch. systemd-timesyncd is dropped from the systemd build
# entirely (see recipes-core/systemd/systemd_%.bbappend) so there is no
# competing NTP client to mask here.
