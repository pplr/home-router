SUMMARY = "Static network configuration for Futro S920 router"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

SRC_URI = " \
    file://10-wan.link \
    file://10-lan0.link \
    file://10-lan1.link \
    file://10-wan.network \
    file://15-wan0-100.netdev \
    file://15-wan0-100.network \
    file://20-lan0.network \
    file://20-lan1.network \
    file://25-lan0-100.netdev \
    file://25-lan0-100.network \
    file://25-lan1-100.netdev \
    file://25-lan1-100.network \
    file://25-lan0-30.netdev \
    file://25-lan0-30.network \
    file://25-lan1-30.netdev \
    file://25-lan1-30.network \
    file://30-br-lan.netdev \
    file://30-br-lan.network \
    file://30-br-iptv.netdev \
    file://30-br-iptv.network \
    file://30-br-iot.netdev \
    file://30-br-iot.network \
    file://networkd-router.conf \
    file://resolved-router.conf \
"

S = "${UNPACKDIR}"

RDEPENDS:${PN} = "systemd-networkd"

do_install() {
    install -d ${D}${sysconfdir}/systemd/network
    for f in 10-wan.link 10-lan0.link 10-lan1.link \
             10-wan.network 20-lan0.network 20-lan1.network \
             15-wan0-100.netdev 15-wan0-100.network \
             25-lan0-100.netdev 25-lan0-100.network \
             25-lan1-100.netdev 25-lan1-100.network \
             25-lan0-30.netdev 25-lan0-30.network \
             25-lan1-30.netdev 25-lan1-30.network \
             30-br-lan.netdev 30-br-lan.network \
             30-br-iptv.netdev 30-br-iptv.network \
             30-br-iot.netdev 30-br-iot.network; do
        install -m 0644 ${UNPACKDIR}/$f ${D}${sysconfdir}/systemd/network/$f
    done

    install -d ${D}${sysconfdir}/systemd/networkd.conf.d
    install -m 0644 ${UNPACKDIR}/networkd-router.conf ${D}${sysconfdir}/systemd/networkd.conf.d/router.conf

    install -d ${D}${sysconfdir}/systemd/resolved.conf.d
    install -m 0644 ${UNPACKDIR}/resolved-router.conf ${D}${sysconfdir}/systemd/resolved.conf.d/router.conf
}

# Generate the declarative bits (static-lease drop-ins, resolved local-zone
# drop-in) from config.toml. Attached as a postfunc rather than
# `python do_install:append` because mixing shell `do_X:append` and
# `python do_X:append` text-merges both bodies into the base shell task,
# breaking the shell dep parser.
python do_install_hosts() {
    import pathlib, sys
    sys.path.insert(0, d.getVar("ROUTERBUILD_ROOT"))
    from routerbuild.config import NetworkConfig
    from routerbuild.render import write_network_dropins, write_resolved_dropin

    cfg = NetworkConfig.load(pathlib.Path(d.getVar("CONFIG_TOML")))
    sysconf = pathlib.Path(d.getVar("D") + d.getVar("sysconfdir"))
    write_network_dropins(cfg, sysconf / "systemd" / "network")
    write_resolved_dropin(cfg, sysconf / "systemd" / "resolved.conf.d")
}
do_install[postfuncs] += "do_install_hosts"

# Re-run do_install whenever config.toml or the routerbuild generator
# changes. Without this, bitbake's task-signature would only hash the
# SRC_URI files and silently skip regeneration.
do_install[file-checksums] += " \
    ${CONFIG_TOML}:True \
    ${ROUTERBUILD_ROOT}/routerbuild/config.py:True \
    ${ROUTERBUILD_ROOT}/routerbuild/render.py:True \
"

FILES:${PN} = " \
    ${sysconfdir}/systemd/network/ \
    ${sysconfdir}/systemd/networkd.conf.d/ \
    ${sysconfdir}/systemd/resolved.conf.d/ \
"
