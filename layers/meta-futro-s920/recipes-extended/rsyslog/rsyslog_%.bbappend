# Enable the omjournal output module.
#
# Our syslog-collector recipe forwards remote AP syslog (udp/514) straight
# into the systemd journal via `action(type="omjournal")` (see
# files/10-remote.conf). But OE-core's rsyslog PACKAGECONFIG ships *no*
# omjournal entry — it enables libsystemd and imjournal (journal *input*),
# yet never passes rsyslog's separate `--enable-omjournal` configure flag.
# Result: omjournal.so is never built, rsyslog fails to load the module at
# startup and exits 1 ("module name 'omjournal' is unknown"), taking the
# whole collector down. Add the missing knob and turn it on. It links
# libsystemd's journal API, already pulled in by the `systemd` PACKAGECONFIG.
PACKAGECONFIG[omjournal] = "--enable-omjournal,--disable-omjournal,systemd,"
PACKAGECONFIG:append = " omjournal"

# Replace OE-core's stock /etc/rsyslog.conf with a minimal, journal-only one.
#
# The stock config does traditional on-disk file logging (/var/log/syslog,
# messages, kern.log, …) with `$FileGroup adm`. Our syslog-collector drop-in
# (override.conf) hardens rsyslog down to CAP_NET_BIND_SERVICE + CAP_SYSLOG —
# no CAP_CHOWN — so the chown of each file to group `adm` fails with EPERM and
# logs an err-priority line per file on every boot. /var/log is tmpfs too, so
# those files are wiped each boot and duplicate the persistent journal. Our
# rsyslog.conf drops all of that and keeps only the rsyslog.d include that
# pulls in the remote-AP imudp+omjournal collector. OE-core installs
# ${UNPACKDIR}/rsyslog.conf into ${sysconfdir}; a higher-priority layer copy
# of the same filename wins.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI += "file://rsyslog.conf"
