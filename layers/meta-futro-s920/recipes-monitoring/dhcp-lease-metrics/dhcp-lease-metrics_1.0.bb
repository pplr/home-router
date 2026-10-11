SUMMARY = "DHCP server leases as node_exporter textfile metrics"
DESCRIPTION = " \
    systemd-networkd's DHCP server publishes its leases only as JSON under \
    /var/lib/systemd/network/dhcp-server-lease/. A path unit re-renders them \
    into /run/node-exporter/textfile/dhcp-leases.prom on every change, so \
    node_exporter exposes one router_dhcp_lease_expiry_timestamp_seconds \
    series per lease (bridge, address, mac, hostname) for the Grafana \
    'Router Network' dashboard. \
"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

SRC_URI = " \
    file://dhcp-lease-metrics.sh \
    file://dhcp-lease-metrics.service \
    file://dhcp-lease-metrics.path \
"

S = "${UNPACKDIR}"

inherit systemd allarch

SYSTEMD_SERVICE:${PN} = "dhcp-lease-metrics.path dhcp-lease-metrics.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

# The output directory is created by node-exporter's tmpfiles.d entry.
RDEPENDS:${PN} = "jq node-exporter"

do_install() {
    install -d ${D}${libexecdir}
    install -m 0755 ${UNPACKDIR}/dhcp-lease-metrics.sh ${D}${libexecdir}/dhcp-lease-metrics.sh

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${UNPACKDIR}/dhcp-lease-metrics.service ${UNPACKDIR}/dhcp-lease-metrics.path \
        ${D}${systemd_system_unitdir}/
}

FILES:${PN} = " \
    ${libexecdir}/dhcp-lease-metrics.sh \
    ${systemd_system_unitdir}/dhcp-lease-metrics.service \
    ${systemd_system_unitdir}/dhcp-lease-metrics.path \
"
