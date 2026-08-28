SUMMARY = "Console font, keymap and system locale for Futro S920"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

SRC_URI = "file://vconsole.conf \
           file://locale.conf \
           file://locale.sh \
"
S = "${UNPACKDIR}"

do_install() {
    install -d ${D}${sysconfdir}
    install -m 0644 ${UNPACKDIR}/vconsole.conf ${D}${sysconfdir}/
    install -m 0644 ${UNPACKDIR}/locale.conf ${D}${sysconfdir}/

    # Two consumers, neither redundant: systemd PID1 reads /etc/locale.conf and
    # folds it into every unit's default environment, while /etc/profile.d/ is
    # what an interactive login (SSH or the VT) actually picks up.
    install -d ${D}${sysconfdir}/profile.d
    install -m 0644 ${UNPACKDIR}/locale.sh ${D}${sysconfdir}/profile.d/locale.sh
}
