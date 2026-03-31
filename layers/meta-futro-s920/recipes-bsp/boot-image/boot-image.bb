SUMMARY = "Grubenv vfat partition image for WIC rawcopy"
DESCRIPTION = "Creates a 1 MiB vfat image containing the initial RAUC grubenv file."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

DEPENDS = "grub-efi-native dosfstools-native mtools-native"

INHIBIT_DEFAULT_DEPS = "1"

inherit deploy nopackages

do_compile() {
    # Create initial grubenv with slot A active
    grub-editenv ${WORKDIR}/grubenv create
    grub-editenv ${WORKDIR}/grubenv set ORDER="A B"
    grub-editenv ${WORKDIR}/grubenv set A_TRY=0
    grub-editenv ${WORKDIR}/grubenv set B_TRY=0
    grub-editenv ${WORKDIR}/grubenv set A_OK=1
    grub-editenv ${WORKDIR}/grubenv set B_OK=0

    # Create 1 MiB vfat image containing grubenv
    dd if=/dev/zero of=${WORKDIR}/grubenv.vfat bs=1024 count=1024
    mkfs.vfat -n grubenv -i F7200002 ${WORKDIR}/grubenv.vfat
    mcopy -i ${WORKDIR}/grubenv.vfat ${WORKDIR}/grubenv ::/grubenv
}

do_deploy() {
    install -m 0644 ${WORKDIR}/grubenv.vfat ${DEPLOYDIR}/grubenv.vfat
}

addtask deploy after do_compile
