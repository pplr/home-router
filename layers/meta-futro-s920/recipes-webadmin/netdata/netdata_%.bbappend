# Trim PACKAGECONFIG vs upstream default ("openssl freeipmi systemd"):
#   - drop freeipmi  : no BMC on the Futro S920
#   - keep openssl   : TLS support
#   - keep systemd   : links libsystemd so netdata can write its own logs to
#                      journald ([logs] = journal below). NOTE: the systemd
#                      journal *reader* plugin this also builds is disabled at
#                      runtime — see [plugins] systemd-journal = no.
#   - add  webui_v2  : rm older v0/v1 web UIs from the rootfs (recipe ships all
#                      three otherwise)
#   - add  go        : builds go.d.plugin, which provides the `prometheus`
#                      go.d collector that scrapes the OpenWrt APs' node-exporter
#                      (the go.d/*.conf we write below are inert without it).
#                      Gated upstream behind PACKAGECONFIG[go] (-DENABLE_PLUGIN_GO);
#                      its do_compile fetches go modules, so the build host needs
#                      network (do_compile[network]=1 is set when `go` is in).
PACKAGECONFIG = "openssl systemd webui_v2 go"

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

    # Scrape the OpenWrt APs. Each AP runs prometheus-node-exporter-lua on
    # :9100 (see aps/config.toml common.packages); netdata's go.d/prometheus
    # collector pulls http://<ap>:9100/metrics and charts it under the AP's
    # job name. This replaces the old netdata-agent streaming (the packaged
    # OpenWrt netdata was badly outdated) — pull instead of push, no shared
    # secret, and no stale agent on the APs.
    #
    # go.d.conf ships only `logind: yes`; flip the prometheus module on too.
    cat > ${D}${sysconfdir}/netdata/go.d.conf <<'EOF'
modules:
    logind: yes
    prometheus: yes
EOF

    # Virtual nodes: one per AP. Without this, every scraped metric is attached
    # to the router's own node and the three APs are indistinguishable in the
    # dashboard. A vnode gives each AP its own node in netdata (own menu entry,
    # own host labels) even though the data is collected by the router's go.d.
    # The job's `vnode:` value matches a vnode `hostname` below.
    #
    # GUIDs are FIXED (not generated at build time): netdata keys a node's
    # metrics DB on /data by this GUID, so regenerating it per build would
    # orphan the historical data on every RAUC update. Treat each GUID as
    # permanent for that AP; assign a fresh `uuidgen` only when adding a new AP.
    install -d ${D}${sysconfdir}/netdata/vnodes
    cat > ${D}${sysconfdir}/netdata/vnodes/vnodes.conf <<'EOF'
- hostname: ap-ax3600
  guid: fea59aa7-1c15-4137-8482-96912d0d52a5
- hostname: ap-r3g
  guid: 274c21e7-e544-44f0-afa6-4480ca340249
- hostname: ap-ax59u
  guid: c4dbb8a8-48ca-40df-b195-5d894e470a03
EOF

    # One scrape job per AP. IPs mirror the AP management addresses in the
    # repo-root hosts.toml (source of truth); they're inlined here because a
    # bitbake recipe doesn't parse hosts.toml. Keep in sync when adding an AP.
    # Each job's `vnode:` attaches its metrics to the matching vnode above.
    install -d ${D}${sysconfdir}/netdata/go.d
    cat > ${D}${sysconfdir}/netdata/go.d/prometheus.conf <<'EOF'
jobs:
  - name: ap-ax3600
    vnode: ap-ax3600
    url: http://10.0.0.2:9100/metrics
  - name: ap-r3g
    vnode: ap-r3g
    url: http://10.0.0.3:9100/metrics
  - name: ap-ax59u
    vnode: ap-ax59u
    url: http://10.0.0.4:9100/metrics
EOF
}
