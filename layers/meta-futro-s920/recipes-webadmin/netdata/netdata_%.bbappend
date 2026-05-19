# Trim PACKAGECONFIG vs upstream default ("openssl freeipmi systemd"):
#   - drop freeipmi  : no BMC on the Futro S920
#   - keep openssl   : TLS support
#   - keep systemd   : enables the systemd-journal collector
#   - add  webui_v2  : rm older v0/v1 web UIs from the rootfs (recipe ships all
#                      three otherwise)
PACKAGECONFIG = "openssl systemd webui_v2"

# Disable the /sys/class/drm sub-collector. netdata 1.47.5's
# proc.plugin[/sys/class/drm] has a use-after-free that fires on the S920's
# AMD GX-222GC iGPU: gpu_busy_percent is ENOTSUP and mem_busy_percent is
# ENOENT on Jaguar-era amdgpu, and the error path frees a buffer it later
# reads back, tripping glibc's "double free or corruption" abort on the
# first scrape. Symptom: daemon reaches "STARTUP completed" then dies with
# SIGABRT/SIGSEGV ~2s later, restart-looping forever.
do_install:append() {
    cat >> ${D}${sysconfdir}/netdata/netdata.conf <<'EOF'

[plugin:proc]
	/sys/class/drm = no

# Route operational logs to journald instead of /var/log/netdata/*.log:
# - /var/log is a tmpfs on this image, so file-logged netdata output is
#   wiped on every reboot
# - the journal is bind-mounted from /data and survives reboots + A/B swaps
# - netdata 1.47.5's compiled-in defaults are *file* despite the upstream
#   netdata.conf comment claiming "journal" — we set them explicitly here.
# access/debug stay as files (they're noise; access is per-HTTP-request, debug
# is gated by debug flags = 0). aclk.log is unused since "no ACLK" anyway.
[logs]
	daemon = journal
	collector = journal
	health = journal
EOF
}
