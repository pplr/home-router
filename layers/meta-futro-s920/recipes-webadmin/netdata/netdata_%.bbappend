# Trim PACKAGECONFIG vs upstream default ("openssl freeipmi systemd"):
#   - drop freeipmi  : no BMC on the Futro S920
#   - keep openssl   : TLS support
#   - keep systemd   : links libsystemd so netdata can write its own logs to
#                      journald ([logs] = journal below). NOTE: the systemd
#                      journal *reader* plugin this also builds is disabled at
#                      runtime — see [plugins] systemd-journal = no.
#   - add  webui_v2  : rm older v0/v1 web UIs from the rootfs (recipe ships all
#                      three otherwise)
PACKAGECONFIG = "openssl systemd webui_v2"

# Streaming parent: accept metrics streamed from the OpenWrt APs that run
# netdata as children (those with `netdata = true` in aps/config.toml). The
# shared streaming API key is an out-of-tree secret (a UUID) — the SAME value
# must live in each child AP's aps/secrets/netdata/stream_api_key. Resolved
# from SECRETS_DIR/netdata via FILESEXTRAPATHS, like openssh's host keys; a
# missing file fails the build at do_fetch naming the expected path.
FILESEXTRAPATHS:prepend := "${SECRETS_DIR}/netdata:"
SRC_URI += " file://stream_api_key"

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

# Disable the systemd-journal *reader* plugin (the dashboard "Logs" tab).
# netdata's .service sets LogNamespace=netdata, so all netdata output lands in
# the `netdata` journal namespace (/var/log/journal/<machine-id>.netdata/ on
# /data). The reader plugin scans /var/log/journal and cannot open the *active*
# system.journal of that namespace ("JOURNAL: cannot open file ... to update
# msg_ut"); it logs the failure back INTO that same namespace, which retriggers
# it — a self-feeding loop running several times/second that bloated the
# namespace to ~240 MB. We don't use the in-dashboard log viewer (logs are
# already in journald: `journalctl --namespace=netdata`), so turn the plugin
# off. This does NOT affect metrics, streaming, or netdata's own journald
# logging above.
[plugins]
	systemd-journal = no
EOF

    # Streaming parent registration. The child APs send this UUID as their
    # stream "api key"; the parent only ingests keys it has an enabled
    # section for. Key is read from the out-of-tree secret (stripped of any
    # trailing newline by the command substitution).
    #
    # MUST be world-readable (0644): netdata's load_stream_conf()
    # (src/streaming/rrdpush.c) reads stream.conf AFTER dropping to the
    # `netdata` user, and on open failure it *silently* falls back to the
    # stock config — which has no [<UUID>] section, so the parent rejects
    # every child with "API key is not enabled". A 0640 root:root file
    # reproduces exactly that. The key is only a LAN streaming token and the
    # router is root-only access, so world-readable is acceptable here.
    # NOTE: `allow from` is a netdata SIMPLE_PATTERN matched against the
    # client IP *string* (rrdpush.c simple_pattern_create/-_matches), NOT a
    # CIDR. `10.0.0.0/24` never matches `10.0.0.4` — use a glob (`10.0.0.*`).
    stream_api_key="$(cat ${UNPACKDIR}/stream_api_key)"
    cat > ${D}${sysconfdir}/netdata/stream.conf <<EOF
[${stream_api_key}]
	enabled = yes
	allow from = 10.0.0.*
	default memory mode = dbengine
	health enabled by default = auto
EOF
    chmod 0644 ${D}${sysconfdir}/netdata/stream.conf
}
