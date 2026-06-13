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
