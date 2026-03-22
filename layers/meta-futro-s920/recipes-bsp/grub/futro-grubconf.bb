SUMMARY = "GRUB A/B boot configuration for Futro S920 with RAUC"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

RPROVIDES:${PN} += "virtual-grub-bootconf"

require conf/image-uefi.conf

SRC_URI = "file://grub.cfg"

S = "${UNPACKDIR}"

do_install() {
    install -d ${D}${EFI_FILES_PATH}
    install -m 0644 ${UNPACKDIR}/grub.cfg ${D}${EFI_FILES_PATH}/grub.cfg
}

FILES:${PN} = "${EFI_FILES_PATH}/grub.cfg"
