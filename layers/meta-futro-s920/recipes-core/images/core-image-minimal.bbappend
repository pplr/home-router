IMAGE_INSTALL:append = " rauc sudo systemd-networkd futro-network-conf openssh-sshd kernel-modules kbd kbd-consolefonts kbd-keymaps futro-console-conf"

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
"

IMAGE_FSTYPES:append = " ext4"

IMAGE_CLASSES += "extrausers"
EXTRA_USERS_PARAMS = "\
    useradd -m -s /bin/sh -G sudo -p '\$6\$t6sjNgqbM7cJxgyY\$0ju1OPcLUWQ2fRuFjvRzxj87nOr8kgBlFIfArdcWq/aJgmiNBfqWxU9VcP4oruPcRLfL4b.rw57ciXEg3jNv50' pplr; \
    passwd-expire pplr; \
"

remove_shadow_backups() {
    rm -f ${IMAGE_ROOTFS}${sysconfdir}/shadow- \
          ${IMAGE_ROOTFS}${sysconfdir}/passwd- \
          ${IMAGE_ROOTFS}${sysconfdir}/group- \
          ${IMAGE_ROOTFS}${sysconfdir}/gshadow-
}
ROOTFS_POSTPROCESS_COMMAND += "remove_shadow_backups;"
