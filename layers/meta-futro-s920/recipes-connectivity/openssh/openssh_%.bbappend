FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI += "file://lan-only.conf"

do_install:append() {
    install -d ${D}${sysconfdir}/ssh/sshd_config.d
    install -m 0644 ${UNPACKDIR}/lan-only.conf ${D}${sysconfdir}/ssh/sshd_config.d/lan-only.conf
}
