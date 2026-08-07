IMAGE_INSTALL:append = " rauc sudo systemd-networkd futro-network-conf futro-firewall futro-persistent-state futro-ap-certs victoria-metrics victoria-logs node-exporter systemd-journal-upload chrony tzdata-core openssh-sshd openssh-ssh openssh-sftp-server avahi-daemon avahi-utils kernel-modules kbd kbd-consolefonts kbd-keymaps futro-console-conf linux-firmware-amdgpu linux-firmware-rtl8168 fbset"

# Show boot messages instead of splash screen
SPLASH = ""

# Replacements for busybox applets removed by the coreutils switch
IMAGE_INSTALL:append = " \
    procps \
    iproute2 \
    util-linux \
    grep \
    sed \
    gawk \
    findutils \
    tar \
    gzip \
    bzip2 \
    xz \
    less \
    diffutils \
    patch \
    iputils-ping \
    wget \
    psmisc \
    kmod \
    ncurses-tools \
    vim-tiny \
    bind-utils \
    which \
    unzip \
    cpio \
    tcpdump \
"

IMAGE_FSTYPES:append = " ext4"

IMAGE_CLASSES += "extrausers"

# Password hash for `pplr` is read from an out-of-tree, gitignored file at parse
# time. See layers/meta-futro-s920/files/secrets/README.md for generation.
# Drop `passwd-expire`: with static config the baked hash *is* the live password,
# so a forced first-login change would just be wiped on the next RAUC update.
def read_secret(d, name):
    import os
    path = os.path.join(d.getVar('SECRETS_DIR'), name)
    if not os.path.exists(path):
        bb.fatal("Missing secret '%s' (expected at %s). See files/secrets/README.md." % (name, path))
    with open(path) as f:
        return f.read().strip()

# Escape $ so it survives the double-quoted shell assignment
# `user_group_settings="${EXTRA_USERS_PARAMS}"` inside extrausers.bbclass —
# otherwise $6, $rounds, $<salt>, $<hash> are expanded as empty shell vars
# before useradd ever sees the hash.
PPLR_PASSWD_HASH := "${@read_secret(d, 'pplr.hash').replace('$', r'\$')}"

EXTRA_USERS_PARAMS = "useradd -m -s /bin/sh -G sudo,adm -p '${PPLR_PASSWD_HASH}' pplr;"

remove_shadow_backups() {
    rm -f ${IMAGE_ROOTFS}${sysconfdir}/shadow- \
          ${IMAGE_ROOTFS}${sysconfdir}/passwd- \
          ${IMAGE_ROOTFS}${sysconfdir}/group- \
          ${IMAGE_ROOTFS}${sysconfdir}/gshadow-
}
ROOTFS_POSTPROCESS_COMMAND += "remove_shadow_backups;"
