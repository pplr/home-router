SUMMARY = "Remote syslog collector for the home-network APs"
DESCRIPTION = " \
    Configures rsyslog to listen on udp/514 bound to 10.0.0.1 (LAN \
    only) and forward incoming messages from 10.0.0.0/24 into the \
    systemd journal via omjournal. Lets `journalctl _HOSTNAME=ap-…` \
    surface the OpenWrt APs' logs alongside the router's own. \
"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

SRC_URI = " \
    file://10-remote.conf \
    file://override.conf \
"

S = "${UNPACKDIR}"

inherit systemd

# Pull in the rsyslog daemon itself. Upstream ships its own rsyslog.service
# (Alias=syslog.service); the drop-in below extends rather than replaces it.
RDEPENDS:${PN} = "rsyslog"

# We don't ship our own unit — we layer a drop-in over rsyslog's. No
# SYSTEMD_SERVICE entry needed; the systemd_use class wouldn't enable
# anything here anyway.

do_install() {
    install -d ${D}${sysconfdir}/rsyslog.d
    install -m 0644 ${UNPACKDIR}/10-remote.conf \
        ${D}${sysconfdir}/rsyslog.d/10-remote.conf

    install -d ${D}${systemd_system_unitdir}/rsyslog.service.d
    install -m 0644 ${UNPACKDIR}/override.conf \
        ${D}${systemd_system_unitdir}/rsyslog.service.d/override.conf
}

FILES:${PN} = " \
    ${sysconfdir}/rsyslog.d/10-remote.conf \
    ${systemd_system_unitdir}/rsyslog.service.d/override.conf \
"
