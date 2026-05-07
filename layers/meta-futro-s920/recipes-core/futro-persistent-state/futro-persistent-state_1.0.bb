SUMMARY = "Persistent runtime state on /data for the Futro S920 router"
DESCRIPTION = " \
    Bridges operational state (DHCP server leases, systemd journal) into the \
    /data partition so it survives RAUC A/B updates. Ships a oneshot unit \
    that creates the bind-mount source dirs after /data is mounted, plus a \
    journald drop-in enabling persistent storage. The actual bind mounts are \
    declared in /etc/fstab (see base-files bbappend). \
"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

SRC_URI = " \
    file://futro-data-prep.service \
    file://journald-persistent.conf \
"

S = "${UNPACKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "futro-data-prep.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

RDEPENDS:${PN} = "systemd"

do_install() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${UNPACKDIR}/futro-data-prep.service \
        ${D}${systemd_system_unitdir}/futro-data-prep.service

    install -d ${D}${sysconfdir}/systemd/journald.conf.d
    install -m 0644 ${UNPACKDIR}/journald-persistent.conf \
        ${D}${sysconfdir}/systemd/journald.conf.d/persistent.conf
}

FILES:${PN} = " \
    ${systemd_system_unitdir}/futro-data-prep.service \
    ${sysconfdir}/systemd/journald.conf.d/persistent.conf \
"
