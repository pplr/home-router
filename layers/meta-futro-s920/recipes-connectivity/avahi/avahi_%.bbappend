FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

# Run avahi as an mDNS reflector (IoT <-> LAN discovery). Keep the default
# `dbus` PACKAGECONFIG: avahi-daemon.service ships as `Type=dbus`
# (BusName=org.freedesktop.Avahi), so without D-Bus the daemon runs fine but
# never acquires the bus name — systemd's readiness never fires and the unit is
# SIGTERM'd at the start timeout. dbus is already in the image (systemd pulls
# it), so this costs nothing. Set explicitly to document the dependency.
PACKAGECONFIG = "dbus"

SRC_URI += "file://avahi-daemon.conf"

# Overwrite the stock avahi-daemon.conf (a CONFFILES entry in the base recipe)
# with our reflector config.
do_install:append() {
    install -m 0644 ${UNPACKDIR}/avahi-daemon.conf ${D}${sysconfdir}/avahi/avahi-daemon.conf
}
