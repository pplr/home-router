SUMMARY = "VictoriaMetrics single-node (prebuilt) — router metrics store"
DESCRIPTION = " \
    Ships the upstream prebuilt linux-amd64 victoria-metrics-prod binary and a \
    systemd unit. VictoriaMetrics stores time-series and *scrapes* metrics via \
    its built-in vmagent (-promscrape.config): the router's own node_exporter \
    on 127.0.0.1:9100 plus each OpenWrt AP's prometheus-node-exporter-lua on \
    :9100. Data lives on /data so it survives RAUC A/B swaps. Grafana runs on a \
    separate LAN host and queries the HTTP API on :8428. Prebuilt (a large Go \
    tree) and pinned by sha256. \
"
HOMEPAGE = "https://victoriametrics.com"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = " \
    https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v${PV}/victoria-metrics-linux-amd64-v${PV}.tar.gz \
    file://victoria-metrics.service \
    file://scrape.yml \
"
SRC_URI[sha256sum] = "7d16e5099be040e10019cfe0c03c50043bad4e896b371129cb41be80efd687cc"

S = "${UNPACKDIR}"

COMPATIBLE_HOST = "x86_64.*-linux"

INHIBIT_PACKAGE_STRIP = "1"
INHIBIT_PACKAGE_DEBUG_SPLIT = "1"
INHIBIT_SYSROOT_STRIP = "1"
INSANE_SKIP:${PN} += "already-stripped ldflags"

do_configure[noexec] = "1"
do_compile[noexec] = "1"

inherit useradd systemd

USERADD_PACKAGES = "${PN}"
USERADD_PARAM:${PN} = "--system --no-create-home \
    --home-dir ${localstatedir}/lib/victoria-metrics \
    --shell /usr/sbin/nologin --user-group victoria-metrics"

SYSTEMD_SERVICE:${PN} = "victoria-metrics.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${UNPACKDIR}/victoria-metrics-prod ${D}${bindir}/victoria-metrics

    install -d ${D}${sysconfdir}/victoria-metrics
    install -m 0644 ${UNPACKDIR}/scrape.yml ${D}${sysconfdir}/victoria-metrics/scrape.yml

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${UNPACKDIR}/victoria-metrics.service \
        ${D}${systemd_system_unitdir}/victoria-metrics.service

    # Storage dir; the real one is a bind mount from /data (see fstab). This is
    # the pre-mount placeholder so the service can start even without /data.
    install -d -o victoria-metrics -g victoria-metrics ${D}${localstatedir}/lib/victoria-metrics
}

FILES:${PN} = " \
    ${bindir}/victoria-metrics \
    ${sysconfdir}/victoria-metrics/scrape.yml \
    ${localstatedir}/lib/victoria-metrics \
    ${systemd_system_unitdir}/victoria-metrics.service \
"
