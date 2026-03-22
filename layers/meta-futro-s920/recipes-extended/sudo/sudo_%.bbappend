FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI += "file://sudo-group.conf"

do_install:append() {
    install -d ${D}${sysconfdir}/sudoers.d
    install -m 0440 ${UNPACKDIR}/sudo-group.conf ${D}${sysconfdir}/sudoers.d/sudo-group
}
