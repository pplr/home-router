SUMMARY = "Prometheus node_exporter (prebuilt) — router host metrics"
DESCRIPTION = " \
    Ships the upstream prebuilt linux-amd64 node_exporter binary and a systemd \
    unit that binds it to 127.0.0.1:9100. It is scraped locally by \
    VictoriaMetrics for the router's own host metrics — the LAN-facing \
    counterpart to prometheus-node-exporter-lua running on the OpenWrt APs. \
    Prebuilt rather than built-from-source (a large Go tree); pinned by sha256. \
"
HOMEPAGE = "https://github.com/prometheus/node_exporter"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = " \
    https://github.com/prometheus/node_exporter/releases/download/v${PV}/node_exporter-${PV}.linux-amd64.tar.gz \
    file://node-exporter.service \
"
SRC_URI[sha256sum] = "b51d8a76aa2a9156a55d501aca6276fae09e262259a5e4e831d2c2222f084e63"

S = "${UNPACKDIR}"

COMPATIBLE_HOST = "x86_64.*-linux"

# Prebuilt, statically-linked Go binary: don't try to strip it or split debug.
INHIBIT_PACKAGE_STRIP = "1"
INHIBIT_PACKAGE_DEBUG_SPLIT = "1"
INHIBIT_SYSROOT_STRIP = "1"
INSANE_SKIP:${PN} += "already-stripped ldflags"

do_configure[noexec] = "1"
do_compile[noexec] = "1"

inherit systemd

SYSTEMD_SERVICE:${PN} = "node-exporter.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${UNPACKDIR}/node_exporter-${PV}.linux-amd64/node_exporter \
        ${D}${bindir}/node_exporter

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${UNPACKDIR}/node-exporter.service \
        ${D}${systemd_system_unitdir}/node-exporter.service
}

FILES:${PN} = " \
    ${bindir}/node_exporter \
    ${systemd_system_unitdir}/node-exporter.service \
"
