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
    file://disable-bridge-nf.conf \
    file://br_netfilter.conf \
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

    # Keep L2-switched intra-bridge traffic out of the nftables forward chain:
    # force-load br_netfilter early, then pin its bridge-nf-call-* hooks off so
    # same-bridge LAN traffic (e.g. laptop<->AP, both on br-lan) is plain
    # switching. See disable-bridge-nf.conf / br_netfilter.conf for the full why.
    install -d ${D}${sysconfdir}/sysctl.d
    install -m 0644 ${UNPACKDIR}/disable-bridge-nf.conf \
        ${D}${sysconfdir}/sysctl.d/disable-bridge-nf.conf

    install -d ${D}${sysconfdir}/modules-load.d
    install -m 0644 ${UNPACKDIR}/br_netfilter.conf \
        ${D}${sysconfdir}/modules-load.d/br_netfilter.conf
}

FILES:${PN} = " \
    ${sysconfdir}/nftables.conf \
    ${sysconfdir}/sysctl.d/disable-bridge-nf.conf \
    ${sysconfdir}/modules-load.d/br_netfilter.conf \
    ${systemd_system_unitdir}/nftables.service \
"
