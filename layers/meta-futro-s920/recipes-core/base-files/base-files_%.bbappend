FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

dirs755 += "/data"

SRC_URI += "file://motd-info.sh"

do_install:append() {
    install -d ${D}${sysconfdir}/profile.d
    install -m 0644 ${UNPACKDIR}/motd-info.sh ${D}${sysconfdir}/profile.d/motd-info.sh
}
