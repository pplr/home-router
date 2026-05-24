FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

# Enable IPv6 so chrony can serve the LAN's GUA (2a01:e0a:97f:5432::1).
# The upstream chrony recipe leaves PACKAGECONFIG empty by default, which
# resolves to `--disable-ipv6` and would make chrony refuse to bind the v6
# addresses listed in our chrony.conf.
PACKAGECONFIG:append = " ipv6"

# Our chrony.conf (resolved via FILESEXTRAPATHS) replaces the upstream one
# in the SRC_URI fetch. Additionally mask systemd-timesyncd: two NTP
# clients racing for the system clock causes spurious time jumps. The
# /dev/null symlink is systemd's standard "this unit is masked" mechanism
# and is a no-op if timesyncd is not installed.
do_install:append() {
    install -d ${D}${sysconfdir}/systemd/system
    ln -sf /dev/null ${D}${sysconfdir}/systemd/system/systemd-timesyncd.service
}

FILES:${PN} += "${sysconfdir}/systemd/system/systemd-timesyncd.service"
