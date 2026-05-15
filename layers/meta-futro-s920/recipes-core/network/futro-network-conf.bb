SUMMARY = "Static network configuration for Futro S920 router"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

SRC_URI = " \
    file://10-wan.link \
    file://10-lan0.link \
    file://10-lan1.link \
    file://10-wan.network \
    file://20-lan0.network \
    file://20-lan1.network \
    file://30-br-lan.netdev \
    file://30-br-lan.network \
    file://networkd-router.conf \
    file://resolved-router.conf \
"

S = "${UNPACKDIR}"

RDEPENDS:${PN} = "systemd-networkd"

do_install() {
    install -d ${D}${sysconfdir}/systemd/network
    for f in 10-wan.link 10-lan0.link 10-lan1.link \
             10-wan.network 20-lan0.network 20-lan1.network \
             30-br-lan.netdev 30-br-lan.network; do
        install -m 0644 ${UNPACKDIR}/$f ${D}${sysconfdir}/systemd/network/$f
    done

    install -d ${D}${sysconfdir}/systemd/networkd.conf.d
    install -m 0644 ${UNPACKDIR}/networkd-router.conf ${D}${sysconfdir}/systemd/networkd.conf.d/router.conf

    install -d ${D}${sysconfdir}/systemd/resolved.conf.d
    install -m 0644 ${UNPACKDIR}/resolved-router.conf ${D}${sysconfdir}/systemd/resolved.conf.d/router.conf
}

FILES:${PN} = " \
    ${sysconfdir}/systemd/network/ \
    ${sysconfdir}/systemd/networkd.conf.d/ \
    ${sysconfdir}/systemd/resolved.conf.d/ \
"
