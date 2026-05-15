SUMMARY = "Stateful nftables firewall for the Futro S920 router"
DESCRIPTION = " \
    Ships an inet-family ruleset that treats the WAN side as hostile and \
    trusts the LAN bridge, plus a systemd unit that loads it before \
    systemd-networkd brings interfaces up. systemd-networkd's own IPv4 \
    masquerade rules live in a separate `ip nat` table and are intentionally \
    left untouched so the firewall can be reloaded at runtime without \
    breaking NAT. \
"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

SRC_URI = " \
    file://nftables.conf \
    file://nftables.service \
"

S = "${UNPACKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "nftables.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

RDEPENDS:${PN} = "nftables"

do_install() {
    install -d ${D}${sysconfdir}
    install -m 0644 ${UNPACKDIR}/nftables.conf ${D}${sysconfdir}/nftables.conf

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${UNPACKDIR}/nftables.service \
        ${D}${systemd_system_unitdir}/nftables.service
}

FILES:${PN} = " \
    ${sysconfdir}/nftables.conf \
    ${systemd_system_unitdir}/nftables.service \
"
