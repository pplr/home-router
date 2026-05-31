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

# Replace upstream /etc/hosts with one generated from hosts.toml so every
# declared device is reachable by name (and by FQDN under .lan) from the
# router shell. Runs after the shell do_install so /etc/hosts written by
# upstream base-files is overwritten last.
python do_install:append() {
    import pathlib, sys
    sys.path.insert(0, d.getVar("ROUTERBUILD_ROOT"))
    from routerbuild.config import HostsConfig
    from routerbuild.render import write_etc_hosts

    cfg = HostsConfig.load(pathlib.Path(d.getVar("HOSTS_TOML")))
    out = pathlib.Path(d.getVar("D") + d.getVar("sysconfdir")) / "hosts"
    write_etc_hosts(cfg, out)
}

do_install[file-checksums] += " \
    ${HOSTS_TOML}:True \
    ${ROUTERBUILD_ROOT}/routerbuild/config.py:True \
    ${ROUTERBUILD_ROOT}/routerbuild/render.py:True \
"
