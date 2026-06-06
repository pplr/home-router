FILESEXTRAPATHS:prepend := "${THISDIR}/barebox:"

BAREBOX_CONFIG:futro-s920 = "efi_defconfig"

SRC_URI:append:futro-s920 = " \
    file://bootchooser.cfg \
    file://env \
    file://state.dts \
    file://0001-x86-efi-use-pei-target-and-subsystem-for-binutils-2..patch \
"

# dtc is needed to compile the barebox state description into state.dtb,
# which is deployed next to the barebox EFI binary and installed onto the
# ESP (EFI/barebox/state.dtb) by the bootimg-barebox-efi wic plugin.
DEPENDS:append:futro-s920 = " dtc-native"

do_deploy:append:futro-s920() {
    dtc -I dts -O dtb -o ${DEPLOYDIR}/state.dtb ${UNPACKDIR}/state.dts
}
