SUMMARY = "VictoriaLogs single-node (prebuilt) — central log store"
DESCRIPTION = " \
    Ships the upstream prebuilt linux-amd64 victoria-logs-prod binary and a \
    systemd unit. VictoriaLogs is the fleet's central log store: its native \
    syslog listener (:514/udp) receives RFC3164 syslog straight from the \
    OpenWrt APs, and the router's own journald is shipped in via \
    systemd-journal-upload to /insert/journald (:9428). This recipe also wires \
    that upload path (service drop-in + preset). Data lives on /data so it \
    survives RAUC A/B swaps; Grafana on a separate LAN host queries :9428. \
    Prebuilt (a large Go tree) and pinned by sha256. \
"
HOMEPAGE = "https://docs.victoriametrics.com/victorialogs/"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = " \
    https://github.com/VictoriaMetrics/VictoriaLogs/releases/download/v${PV}/victoria-logs-linux-amd64-v${PV}.tar.gz \
    file://victoria-logs.service \
    file://journal-upload-router.conf \
    file://90-router-journal-upload.preset \
"
SRC_URI[sha256sum] = "d14f585144b8d6813f15e11f0041f487e15e10e5f5e5a31be0311367e93d3494"

S = "${UNPACKDIR}"

COMPATIBLE_HOST = "x86_64.*-linux"

INHIBIT_PACKAGE_STRIP = "1"
INHIBIT_PACKAGE_DEBUG_SPLIT = "1"
INHIBIT_SYSROOT_STRIP = "1"
INSANE_SKIP:${PN} += "already-stripped ldflags"

do_configure[noexec] = "1"
do_compile[noexec] = "1"

inherit useradd systemd

# systemd-journal-upload ships the router's own journal to VictoriaLogs.
RDEPENDS:${PN} = "systemd-journal-upload"

USERADD_PACKAGES = "${PN}"
USERADD_PARAM:${PN} = "--system --no-create-home \
    --home-dir ${localstatedir}/lib/victoria-logs \
    --shell /usr/sbin/nologin --user-group victoria-logs"

SYSTEMD_SERVICE:${PN} = "victoria-logs.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${UNPACKDIR}/victoria-logs-prod ${D}${bindir}/victoria-logs

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${UNPACKDIR}/victoria-logs.service \
        ${D}${systemd_system_unitdir}/victoria-logs.service

    # Point systemd-journal-upload (from the systemd package) at VictoriaLogs.
    install -d ${D}${systemd_system_unitdir}/systemd-journal-upload.service.d
    install -m 0644 ${UNPACKDIR}/journal-upload-router.conf \
        ${D}${systemd_system_unitdir}/systemd-journal-upload.service.d/router.conf

    # Enable systemd-journal-upload.service (owned by the systemd package, so it
    # has no SYSTEMD_SERVICE hook here) via a preset — rootfs preset-all runs in
    # enable-only mode and honors this.
    install -d ${D}${sysconfdir}/systemd/system-preset
    install -m 0644 ${UNPACKDIR}/90-router-journal-upload.preset \
        ${D}${sysconfdir}/systemd/system-preset/90-router-journal-upload.preset

    install -d -o victoria-logs -g victoria-logs ${D}${localstatedir}/lib/victoria-logs
}

FILES:${PN} = " \
    ${bindir}/victoria-logs \
    ${localstatedir}/lib/victoria-logs \
    ${systemd_system_unitdir}/victoria-logs.service \
    ${systemd_system_unitdir}/systemd-journal-upload.service.d/router.conf \
    ${sysconfdir}/systemd/system-preset/90-router-journal-upload.preset \
"
