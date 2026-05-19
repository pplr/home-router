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
    file://ssh_host_ecdsa_key \
    file://ssh_host_ecdsa_key.pub \
"

do_install:append() {
    install -d ${D}${sysconfdir}/ssh/sshd_config.d
    install -m 0644 ${UNPACKDIR}/lan-only.conf ${D}${sysconfdir}/ssh/sshd_config.d/lan-only.conf

    install -d ${D}${sysconfdir}/ssh
    install -m 0600 ${UNPACKDIR}/ssh_host_ed25519_key     ${D}${sysconfdir}/ssh/ssh_host_ed25519_key
    install -m 0644 ${UNPACKDIR}/ssh_host_ed25519_key.pub ${D}${sysconfdir}/ssh/ssh_host_ed25519_key.pub
    install -m 0600 ${UNPACKDIR}/ssh_host_rsa_key         ${D}${sysconfdir}/ssh/ssh_host_rsa_key
    install -m 0644 ${UNPACKDIR}/ssh_host_rsa_key.pub     ${D}${sysconfdir}/ssh/ssh_host_rsa_key.pub
    install -m 0600 ${UNPACKDIR}/ssh_host_ecdsa_key       ${D}${sysconfdir}/ssh/ssh_host_ecdsa_key
    install -m 0644 ${UNPACKDIR}/ssh_host_ecdsa_key.pub   ${D}${sysconfdir}/ssh/ssh_host_ecdsa_key.pub
}

# OE-core's openssh recipe defines FILES:${PN}-sshd as an *explicit* list (see
# openssh.inc — sbindir/sshd, sshd_config, moduli, sshd_check_keys, …). Files
# we add under /etc/ssh/ in our do_install:append therefore fall through to
# the `openssh` meta-package's catch-all FILES glob. We install only
# `openssh-sshd` in core-image-minimal.bbappend, so without an explicit
# reclaim our shipped host keys and lan-only.conf are silently dropped from
# the image, and sshdgenkeys.service regenerates fresh random keys on every
# first boot of a new RAUC slot (defeating the point of baking them in).
FILES:${PN}-sshd += " \
    ${sysconfdir}/ssh/ssh_host_ed25519_key \
    ${sysconfdir}/ssh/ssh_host_ed25519_key.pub \
    ${sysconfdir}/ssh/ssh_host_rsa_key \
    ${sysconfdir}/ssh/ssh_host_rsa_key.pub \
    ${sysconfdir}/ssh/ssh_host_ecdsa_key \
    ${sysconfdir}/ssh/ssh_host_ecdsa_key.pub \
    ${sysconfdir}/ssh/sshd_config.d/lan-only.conf \
"
