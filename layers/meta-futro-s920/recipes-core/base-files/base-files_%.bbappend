FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:${SECRETS_DIR}:"

dirs755 += "/data"

# Hostname is a project-level constant (not secret) committed in this layer.
# machine-id is read from gitignored SECRETS_DIR/machine-id; baking it makes
# the device's systemd identity stable across A/B updates.
SRC_URI += " \
    file://motd-info.sh \
    file://hostname \
    file://machine-id \
"

do_install:append() {
    install -d ${D}${sysconfdir}/profile.d
    install -m 0644 ${UNPACKDIR}/motd-info.sh ${D}${sysconfdir}/profile.d/motd-info.sh

    # Overwrite any /etc/hostname or /etc/machine-id created by upstream base-files.
    install -m 0644 ${UNPACKDIR}/hostname   ${D}${sysconfdir}/hostname
    install -m 0444 ${UNPACKDIR}/machine-id ${D}${sysconfdir}/machine-id
}
