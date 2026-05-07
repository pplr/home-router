FILESEXTRAPATHS:prepend := "${THISDIR}/files:${SECRETS_DIR}/ssh:"

# Host keys are baked from out-of-tree gitignored files in SECRETS_DIR/ssh.
# Shipping pre-generated keys keeps device identity stable across RAUC A/B
# updates (sshdgenkeys.service becomes a no-op since the keys already exist).
SRC_URI += " \
    file://lan-only.conf \
    file://ssh_host_ed25519_key \
    file://ssh_host_ed25519_key.pub \
    file://ssh_host_rsa_key \
    file://ssh_host_rsa_key.pub \
"

do_install:append() {
    install -d ${D}${sysconfdir}/ssh/sshd_config.d
    install -m 0644 ${UNPACKDIR}/lan-only.conf ${D}${sysconfdir}/ssh/sshd_config.d/lan-only.conf

    install -d ${D}${sysconfdir}/ssh
    install -m 0600 ${UNPACKDIR}/ssh_host_ed25519_key     ${D}${sysconfdir}/ssh/ssh_host_ed25519_key
    install -m 0644 ${UNPACKDIR}/ssh_host_ed25519_key.pub ${D}${sysconfdir}/ssh/ssh_host_ed25519_key.pub
    install -m 0600 ${UNPACKDIR}/ssh_host_rsa_key         ${D}${sysconfdir}/ssh/ssh_host_rsa_key
    install -m 0644 ${UNPACKDIR}/ssh_host_rsa_key.pub     ${D}${sysconfdir}/ssh/ssh_host_rsa_key.pub
}
