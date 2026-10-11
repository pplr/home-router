#!/usr/bin/env python3
"""Generate the home-router Grafana dashboards and push them to Grafana.

Usage:
    ./monitoring/grafana/build.py --out DIR    # write dashboard JSON, no network
    ./monitoring/grafana/build.py --push       # create/overwrite them in Grafana

Grafana itself runs on the NAS (out of tree); this script is the source of
truth for its dashboards, so a wiped Grafana is rebuilt with one ``--push``.
Edits made in the Grafana UI are overwritten by the next push — make them here.

Fleet facts (AP hostnames, radio bands, SSID names, the AP trunk port, host
MACs) are read from repo-root ``config.toml`` through the same parsers the AP
and router builds use, so adding an AP or renaming an SSID only needs a re-push.

Datasources are referenced through ``${ds_metrics}``/``${ds_logs}`` dashboard
variables (selected by plugin type), never by UID, so the JSON is portable
across Grafana instances.

Push settings: ``GRAFANA_URL`` (default http://nas.lan:30037) and a service
account token (Editor role) from ``GRAFANA_TOKEN`` or ``~/.config/grafana-token``.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "aps"))

from apbuild.config import CONFIG_PATH, ConfigError, FleetConfig  # noqa: E402
from routerbuild.config import NetworkConfig  # noqa: E402

FOLDER_UID = "home-router"
FOLDER_TITLE = "Home Router"
TAG = "home-router"
ROUTER = 'instance="home-router"'

DS_M = {"type": "victoriametrics-metrics-datasource", "uid": "${ds_metrics}"}
DS_L = {"type": "victoriametrics-logs-datasource", "uid": "${ds_logs}"}

BAND_NAMES = {"2g": "2.4 GHz", "5g": "5 GHz", "6g": "6 GHz", "60g": "60 GHz"}

# Syslog severities, most to least severe — VictoriaLogs normalises both the
# journald PRIORITY and the AP syslog severity into this `level` field.
LEVELS = ["emerg", "alert", "crit", "error", "warning", "notice", "info", "debug"]
LEVEL_COLORS = {
    "emerg": "dark-red", "alert": "dark-red", "crit": "red", "error": "red",
    "warning": "orange", "notice": "blue", "info": "green", "debug": "text",
}


# --------------------------------------------------------------------------
# Fleet facts from config.toml
# --------------------------------------------------------------------------

class Fleet:
    def __init__(self) -> None:
        fleet = FleetConfig.load()
        net = NetworkConfig.load(CONFIG_PATH)
        self.aps = sorted(ap.hostname for ap in fleet.aps.values())
        self.trunk = fleet.common.uplink.trunk_port_alias
        self.ssids = [s.name for s in fleet.common.ssids]
        # OpenWrt names each VAP phy<radio index>-ap<n>, n counting the
        # wifi-iface sections on that radio — every SSID on every enabled
        # radio, in config order (see apbuild.render).
        self.bands = {
            f"{ap.hostname}/phy{r.index}": BAND_NAMES.get(r.band or "", r.band or "?")
            for ap in fleet.aps.values() for r in ap.radios if not r.disabled
        }
        self.macs = {h.mac.lower(): h.name for h in net.hosts if h.mac}
        self.pools = {"br-lan": net.router.dhcp_lan.pool_size,
                      "br-iot": net.router.dhcp_iot.pool_size}

    def mac_map(self, upper: bool = False) -> list:
        """Value mapping naming the [[hosts]] MACs (AP exporters print them upper-case)."""
        return [{"type": "value", "options": {
            (m := mac.upper() if upper else mac): {"text": f"{name} ({m})", "index": i}
            for i, (mac, name) in enumerate(self.macs.items())}}]

    def wifi(self, q: str, src: str = "ifname") -> str:
        """Add `band` and `ssid` labels to a series keyed by a VAP name."""
        q = f'label_replace({q}, "band", "$1", "{src}", "(phy[0-9]+)-ap[0-9]+")'
        q = f'label_replace({q}, "ssid", "$1", "{src}", "phy[0-9]+-ap([0-9]+)")'
        q = f'label_join({q}, "band", "/", "instance", "band")'
        bands = ", ".join(f'"{k}", "{v}"' for k, v in self.bands.items())
        ssids = ", ".join(f'"{i}", "{n}"' for i, n in enumerate(self.ssids))
        return f'label_map(label_map({q}, "band", {bands}), "ssid", {ssids})'


# --------------------------------------------------------------------------
# Panel building blocks
# --------------------------------------------------------------------------

def prom(expr: str, legend: str = "", *, instant: bool = False,
         table: bool = False, interval: str | None = None) -> dict:
    t = {"datasource": DS_M, "expr": expr, "legendFormat": legend,
         "instant": instant, "range": not instant,
         "format": "table" if table else "time_series"}
    if interval:
        t["interval"] = interval
    return t


def logsq(expr: str, qt: str = "statsRange", **kw) -> dict:
    return {"datasource": DS_L, "expr": expr, "queryType": qt,
            "editorMode": "code", **kw}


def steps(*pairs) -> dict:
    """steps("green", 70, "orange", 90, "red") → absolute thresholds."""
    out = [{"color": pairs[0], "value": None}]
    for i in range(1, len(pairs), 2):
        out.append({"color": pairs[i + 1], "value": pairs[i]})
    return {"mode": "absolute", "steps": out}


def vmap(mapping: dict) -> list:
    """{value: (text, color)} → Grafana value mappings."""
    return [{"type": "value", "options": {
        str(k): {"text": t, "color": c, "index": i}
        for i, (k, (t, c)) in enumerate(mapping.items())}}]


def ovr(matcher: str, value: str, **props) -> dict:
    kind = "byRegexp" if matcher == "regex" else "byName"
    return {"matcher": {"id": kind, "options": value},
            "properties": [{"id": k.replace("__", "."), "value": v}
                           for k, v in props.items()]}


def mirror(regex: str) -> dict:
    """Draw matching series below the axis (upload/tx under download/rx)."""
    return {"matcher": {"id": "byRegexp", "options": regex},
            "properties": [{"id": "custom.transform", "value": "negative-Y"},
                           {"id": "custom.stacking",
                            "value": {"mode": "normal", "group": "neg"}}]}


def color(name: str, c: str) -> dict:
    return ovr("name", name, color={"mode": "fixed", "fixedColor": c})


def panel(kind: str, title: str, targets: list, *, w: int, h: int,
          unit: str | None = None, desc: str = "", decimals: int | None = None,
          min: float | None = None, max: float | None = None,
          thresholds: dict | None = None, mappings: list | None = None,
          overrides: list | None = None, options: dict | None = None,
          custom: dict | None = None, transformations: list | None = None,
          color_mode: str | None = None, **extra) -> dict:
    defaults: dict = {"custom": custom or {}}
    for k, v in (("unit", unit), ("decimals", decimals), ("min", min),
                 ("max", max), ("thresholds", thresholds), ("mappings", mappings)):
        if v is not None:
            defaults[k] = v
    if color_mode:
        defaults["color"] = {"mode": color_mode}
    for i, t in enumerate(targets):
        t.setdefault("refId", chr(ord("A") + i))
    p = {"type": kind, "title": title, "description": desc,
         "datasource": targets[0]["datasource"] if targets else DS_M,
         "targets": targets, "options": options or {},
         "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
         "_w": w, "_h": h, **extra}
    if transformations:
        p["transformations"] = transformations
    return p


def ts(title: str, targets: list, *, w: int = 12, h: int = 8, stack: bool = False,
       bars: bool = False, fill: int = 15, legend: str = "list",
       calcs: tuple = ("mean", "max"), **kw) -> dict:
    custom = {
        "drawStyle": "bars" if bars else "line", "lineWidth": 1,
        "fillOpacity": 80 if bars else fill, "gradientMode": "none" if bars else "opacity",
        "showPoints": "never", "spanNulls": False, "axisSoftMin": 0,
        "stacking": {"mode": "normal" if stack else "none", "group": "A"},
    }
    custom.update(kw.pop("custom", {}))
    kw.setdefault("color_mode", "palette-classic")
    opts = {"legend": {"showLegend": True, "displayMode": legend,
                       "placement": "bottom", "calcs": list(calcs) if legend == "table" else []},
            "tooltip": {"mode": "multi", "sort": "desc"}}
    return panel("timeseries", title, targets, w=w, h=h, custom=custom,
                 options=opts, **kw)


def stat(title: str, targets: list, *, w: int = 3, h: int = 4, graph: bool = True,
         color_mode: str = "value", text_mode: str = "auto", **kw) -> dict:
    kw.setdefault("thresholds", steps("green"))
    opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": color_mode, "graphMode": "area" if graph else "none",
            "textMode": text_mode, "justifyMode": "auto", "orientation": "auto",
            "showPercentChange": False, "wideLayout": True}
    return panel("stat", title, targets, w=w, h=h, options=opts,
                 color_mode="thresholds", **kw)


def bargauge(title: str, targets: list, *, w: int = 8, h: int = 8, **kw) -> dict:
    opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "displayMode": "gradient", "orientation": "horizontal",
            "showUnfilled": True, "valueMode": "color", "namePlacement": "left",
            "sizing": "auto"}
    return panel("bargauge", title, targets, w=w, h=h, options=opts,
                 color_mode="thresholds", **kw)


UP_MAP = vmap({0: ("DOWN", "red"), 1: ("UP", "green")})


def timeline(title: str, targets: list, *, w: int = 12, h: int = 6, **kw) -> dict:
    opts = {"showValue": "never", "mergeValues": True, "alignValue": "left",
            "rowHeight": 0.8, "legend": {"showLegend": False},
            "tooltip": {"mode": "single"}}
    return panel("state-timeline", title, targets, w=w, h=h, options=opts,
                 custom={"lineWidth": 0, "fillOpacity": 80},
                 color_mode="thresholds", **kw)


def table(title: str, targets: list, *, w: int = 24, h: int = 8,
          sort: str | None = None, desc_sort: bool = True, **kw) -> dict:
    opts = {"showHeader": True, "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["sum"], "fields": ""}}
    if sort:
        opts["sortBy"] = [{"displayName": sort, "desc": desc_sort}]
    return panel("table", title, targets, w=w, h=h, options=opts,
                 custom={"align": "auto", "cellOptions": {"type": "auto"},
                         "filterable": False}, **kw)


def logs_panel(title: str, expr: str, *, w: int = 24, h: int = 14,
               max_lines: int = 1000, desc: str = "") -> dict:
    opts = {"showTime": True, "showLabels": False, "showCommonLabels": False,
            "wrapLogMessage": True, "prettifyLogMessage": False,
            "enableLogDetails": True, "enableInfiniteScrolling": True,
            "dedupStrategy": "none", "sortOrder": "Descending"}
    return panel("logs", title, [logsq(expr, "instant", maxLines=max_lines)],
                 w=w, h=h, options=opts, desc=desc)


def merge_table(rename: dict, drop: tuple = ("Time", "job", "__name__")) -> list:
    """Join instant table queries on their labels; rename `Value #X` columns."""
    order = {name: i for i, name in enumerate(rename.values())}
    return [{"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {k: True for k in drop},
                "renameByName": rename, "indexByName": order}}]


def layout(items: list) -> list:
    """Flow panels left-to-right in 24-column rows; ("row", title) starts a row."""
    out, x, y, row_h, pid = [], 0, 0, 0, 1
    for it in items:
        if isinstance(it, tuple):
            y += row_h
            x, row_h = 0, 0
            out.append({"type": "row", "title": it[1], "collapsed": False,
                        "id": pid, "panels": [],
                        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}})
            y += 1
        else:
            w, h = it.pop("_w"), it.pop("_h")
            if x + w > 24:
                y += row_h
                x, row_h = 0, 0
            it["id"] = pid
            it["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
            out.append(it)
            x += w
            row_h = max(row_h, h)
        pid += 1
    return out


def ds_var(name: str, label: str, plugin: str) -> dict:
    return {"type": "datasource", "name": name, "label": label, "query": plugin,
            "hide": 0, "refresh": 1, "regex": "", "current": {}, "options": []}


def custom_var(name: str, label: str, values: list, *, all_value: str = ".*",
               include_all: bool = True) -> dict:
    opts = ([{"text": "All", "value": "$__all", "selected": True}] if include_all else [])
    opts += [{"text": v, "value": v, "selected": False} for v in values]
    return {"type": "custom", "name": name, "label": label, "query": ",".join(values),
            "multi": True, "includeAll": include_all, "allValue": all_value,
            "current": {"text": ["All"], "value": ["$__all"]}, "options": opts,
            "hide": 0}


def dashboard(uid: str, title: str, panels: list, *, desc: str,
              variables: list = (), time_from: str = "now-6h", refresh: str = "1m") -> dict:
    return {
        "uid": uid, "title": title, "description": desc, "tags": [TAG],
        "timezone": "browser", "editable": True, "graphTooltip": 1,
        "schemaVersion": 41, "refresh": refresh,
        "time": {"from": time_from, "to": "now"},
        "templating": {"list": [
            ds_var("ds_metrics", "Metrics", DS_M["type"]),
            ds_var("ds_logs", "Logs", DS_L["type"]),
            *variables]},
        "links": [{"type": "dashboards", "tags": [TAG], "asDropdown": False,
                   "includeVars": False, "keepTime": True, "title": "Home network",
                   "icon": "external link", "targetBlank": False}],
        "annotations": {"list": []},
        "panels": layout(panels),
    }


def rate(metric: str, sel: str) -> str:
    return f"rate({metric}{{{sel}}}[$__rate_interval])"


# --------------------------------------------------------------------------
# Dashboards
# --------------------------------------------------------------------------

def router_overview(f: Fleet) -> dict:
    R = ROUTER
    cores = f'count(node_cpu_seconds_total{{{R},mode="idle"}})'
    fs = f'{R},mountpoint=~"/|/boot|/data"'
    pct = steps("green", 70, "orange", 90, "red")
    return dashboard("home-router-overview", "Router Overview", desc=(
        "Host health of the Futro S920 router: CPU, memory, storage, kernel "
        "tables, time sync and the scrape targets of its VictoriaMetrics."), panels=[
        stat("Uptime", [prom(f"time() - node_boot_time_seconds{{{R}}}")],
             unit="dtdurations", graph=False, decimals=0),
        stat("CPU busy", [prom(f'100 * (1 - avg({rate("node_cpu_seconds_total", R + ",mode=\"idle\"")}))')],
             unit="percent", thresholds=pct, decimals=0),
        stat("Load (5m)", [prom(f"node_load5{{{R}}}")], decimals=2,
             thresholds=steps("green", 3, "orange", 4, "red"),
             desc="4 cores — sustained load above 4 means runnable tasks are queueing."),
        stat("Memory used", [prom(f"100 * (1 - node_memory_MemAvailable_bytes{{{R}}} / node_memory_MemTotal_bytes{{{R}}})")],
             unit="percent", thresholds=pct, decimals=0),
        stat("Rootfs used", [prom(f'100 * (1 - node_filesystem_avail_bytes{{{R},mountpoint="/"}} / node_filesystem_size_bytes{{{R},mountpoint="/"}})')],
             unit="percent", thresholds=pct, decimals=0, graph=False,
             desc="Active RAUC slot (1 GiB partition)."),
        stat("/data used", [prom(f'100 * (1 - node_filesystem_avail_bytes{{{R},mountpoint="/data"}} / node_filesystem_size_bytes{{{R},mountpoint="/data"}})')],
             unit="percent", thresholds=pct, decimals=1,
             desc="Persistent partition: journal, DHCP leases, ACME state and the VictoriaMetrics/VictoriaLogs databases."),
        stat("APU temperature", [prom(f"max(node_hwmon_temp_celsius{{{R}}})")],
             unit="celsius", thresholds=steps("green", 70, "orange", 85, "red"), decimals=0,
             desc="amdgpu `edge` sensor of the GX-222GC SoC — the only thermal sensor the board exposes."),
        stat("Conntrack", [prom(f"100 * node_nf_conntrack_entries{{{R}}} / node_nf_conntrack_entries_limit{{{R}}}")],
             unit="percent", thresholds=steps("green", 60, "orange", 85, "red"), decimals=1,
             desc="Connection-tracking table fill. At 100 % new flows are dropped."),

        ("row", "CPU & memory"),
        ts("CPU usage by mode", [prom(
            f'sum by (mode) ({rate("node_cpu_seconds_total", R + ",mode!=\"idle\"")}) / scalar({cores}) * 100',
            "{{mode}}")], stack=True, unit="percent", max=100, legend="table"),
        ts("Load average", [prom(f"node_load1{{{R}}}", "1m"), prom(f"node_load5{{{R}}}", "5m"),
                            prom(f"node_load15{{{R}}}", "15m"), prom(cores, "cores")],
           decimals=2, overrides=[ovr("name", "cores", custom__lineStyle={"fill": "dash", "dash": [10, 10]},
                                      custom__fillOpacity=0, color={"mode": "fixed", "fixedColor": "red"})]),
        ts("Memory", [
            prom(f"node_memory_MemTotal_bytes{{{R}}} - node_memory_MemFree_bytes{{{R}}} - node_memory_Buffers_bytes{{{R}}} - node_memory_Cached_bytes{{{R}}} - node_memory_SReclaimable_bytes{{{R}}}", "used"),
            prom(f"node_memory_Cached_bytes{{{R}}} + node_memory_SReclaimable_bytes{{{R}}}", "cache"),
            prom(f"node_memory_Buffers_bytes{{{R}}}", "buffers"),
            prom(f"node_memory_MemFree_bytes{{{R}}}", "free")],
           stack=True, unit="bytes", min=0, legend="table", calcs=("lastNotNull", "max"),
           overrides=[color("used", "orange"), color("cache", "blue"),
                      color("buffers", "purple"), color("free", "green")]),
        ts("APU temperature", [prom(f"node_hwmon_temp_celsius{{{R}}}", "edge")],
           w=6, unit="celsius", custom={"axisSoftMin": 30}),
        ts("CPU frequency", [prom(f"avg(node_cpu_scaling_frequency_hertz{{{R}}})", "avg"),
                             prom(f"max(node_cpu_scaling_frequency_max_hertz{{{R}}})", "max")],
           w=6, unit="hertz"),

        ("row", "Storage"),
        bargauge("Filesystem usage", [prom(
            f"100 * (1 - node_filesystem_avail_bytes{{{fs}}} / node_filesystem_size_bytes{{{fs}}})",
            "{{mountpoint}}")], unit="percent", min=0, max=100, thresholds=pct, h=7),
        bargauge("Filesystem free", [prom(f"node_filesystem_avail_bytes{{{fs}}}", "{{mountpoint}}")],
                 unit="bytes", h=7, thresholds=steps("blue")),
        ts("/data usage", [prom(f'node_filesystem_size_bytes{{{R},mountpoint="/data"}} - node_filesystem_avail_bytes{{{R},mountpoint="/data"}}', "used")],
           w=8, h=7, unit="bytes",
           desc="Growth here is mostly the VictoriaMetrics and VictoriaLogs databases (12-month retention)."),
        ts("SSD throughput", [prom(rate("node_disk_read_bytes_total", R + ',device="sda"'), "read"),
                              prom(rate("node_disk_written_bytes_total", R + ',device="sda"'), "write")],
           w=8, unit="Bps", overrides=[mirror("write")]),
        ts("SSD IOPS", [prom(rate("node_disk_reads_completed_total", R + ',device="sda"'), "read"),
                        prom(rate("node_disk_writes_completed_total", R + ',device="sda"'), "write")],
           w=8, unit="iops", overrides=[mirror("write")]),
        ts("SSD busy", [prom(f'100 * {rate("node_disk_io_time_seconds_total", R + ",device=\"sda\"")}', "utilisation")],
           w=8, unit="percent", max=100),
        ts("SSD writes per day", [prom(f'increase(node_disk_written_bytes_total{{{R},device="sda"}}[1d])',
                                       "written", interval="1d")],
           w=24, h=6, bars=True, unit="decbytes", timeFrom="30d", hideTimeOverride=False,
           desc="Daily volume written to the 8 GB Innodisk mSATA — the figure that matters for flash wear."),

        ("row", "Kernel & network stack"),
        ts("Conntrack entries", [prom(f"node_nf_conntrack_entries{{{R}}}", "entries"),
                                 prom(f"node_nf_conntrack_entries_limit{{{R}}}", "limit")], w=8,
           overrides=[color("limit", "red")]),
        ts("Sockets", [prom(f"node_sockstat_TCP_inuse{{{R}}}", "TCP in use"),
                       prom(f"node_sockstat_TCP_tw{{{R}}}", "TCP time-wait"),
                       prom(f"node_sockstat_UDP_inuse{{{R}}}", "UDP in use")], w=8),
        ts("Packet processing drops", [
            prom(f'sum({rate("node_softnet_dropped_total", R)})', "softnet dropped"),
            prom(f'sum({rate("node_softnet_times_squeezed_total", R)})', "softnet squeezed"),
            prom(f'{rate("node_netstat_Udp_RcvbufErrors", R)}', "UDP rcvbuf errors"),
            prom(f'{rate("node_vmstat_oom_kill", R)}', "OOM kills")], w=8, unit="ops",
           desc="Non-zero softnet drops mean the CPU can't keep up with incoming packets. Squeezes alone are harmless."),
        ts("Clock offset", [prom(f"node_timex_offset_seconds{{{R}}}", "offset"),
                            prom(f"node_timex_estimated_error_seconds{{{R}}}", "estimated error")],
           unit="s", custom={"axisSoftMin": None}, desc="Kernel clock discipline, driven by chrony."),
        stat("Clock synchronised", [prom(f"node_timex_sync_status{{{R}}}")], w=4, h=8, graph=False,
             color_mode="background",
             mappings=vmap({0: ("NO", "red"), 1: ("YES", "green")})),
        stat("Context switches", [prom(f'{rate("node_context_switches_total", R)}')], w=4, h=8,
             unit="ops", thresholds=steps("blue")),
        stat("Processes", [prom(f"node_procs_running{{{R}}}", "running"),
                           prom(f"node_procs_blocked{{{R}}}", "blocked")],
             w=4, h=8, text_mode="value_and_name", thresholds=steps("blue")),

        ("row", "Scrape targets"),
        timeline("Target status", [prom("up", "{{instance}}")], mappings=UP_MAP,
                 thresholds=steps("red", 1, "green"),
                 desc="Every target scraped by the router's VictoriaMetrics (router node_exporter + AP exporters)."),
        ts("Scrape duration", [prom("scrape_duration_seconds and on (instance) up == 1", "{{instance}}")], h=6, unit="s"),
    ])


def router_network(f: Fleet) -> dict:
    R = ROUTER
    b = lambda dev: f'{R},device="{dev}"'  # noqa: E731
    bits = lambda m, sel: f"8 * {rate(m, sel)}"  # noqa: E731
    RX, TX = "node_network_receive_bytes_total", "node_network_transmit_bytes_total"
    ifaces = f'{R},device!~"lo|sit0"'
    # The wan0 counters include the tagged IPTV frames demuxed into wan0.100.
    internet_rx = f'clamp_min(sum({bits(RX, b("wan0"))}) - sum({bits(RX, b("wan0.100"))}), 0)'
    internet_tx = f'clamp_min(sum({bits(TX, b("wan0"))}) - sum({bits(TX, b("wan0.100"))}), 0)'
    # sum() drops the device label so wan0 − wan0.100 can match.
    lease = lambda br=".*": f'router_dhcp_lease_expiry_timestamp_seconds{{{R},bridge=~"{br}"}}'  # noqa: E731
    day = lambda m, dev: f'sum(increase({m}{{{b(dev)}}}[1d]))'  # noqa: E731
    return dashboard("home-router-network", "Router Network", desc=(
        "WAN (Freebox), LAN / IoT bridges, IPTV VLAN and per-interface health of the router."), panels=[
        stat("Internet ↓", [prom(internet_rx)], unit="bps", thresholds=steps("blue"),
             desc="wan0 receive minus the IPTV VLAN (wan0.100)."),
        stat("Internet ↑", [prom(internet_tx)], unit="bps", thresholds=steps("purple")),
        stat("IPTV ↓", [prom(bits(RX, b("wan0.100")))], unit="bps", thresholds=steps("text"),
             desc="Freebox Player traffic on VLAN 100, switched (not routed) to the LAN ports."),
        stat("Internet ↓ 24 h", [prom(f'clamp_min({day(RX, "wan0")} - {day(RX, "wan0.100")}, 0)')],
             unit="decbytes", graph=False, thresholds=steps("blue")),
        stat("Internet ↑ 24 h", [prom(f'clamp_min({day(TX, "wan0")} - {day(TX, "wan0.100")}, 0)')],
             unit="decbytes", graph=False, thresholds=steps("purple")),
        stat("LAN neighbours", [prom(f'node_arp_entries{{{b("br-lan")}}}')], thresholds=steps("green"),
             desc="IPv4 ARP entries on br-lan (includes stale entries)."),
        stat("IoT neighbours", [prom(f'node_arp_entries{{{b("br-iot")}}}')], thresholds=steps("orange"),
             desc="IPv4 ARP entries on br-iot (includes stale entries)."),
        stat("WAN link", [prom(f'node_network_up{{{b("wan0")}}}')], graph=False, color_mode="background",
             mappings=UP_MAP),

        ("row", "WAN"),
        ts("WAN throughput", [prom(internet_rx, "internet ↓"), prom(bits(RX, b("wan0.100")), "IPTV ↓"),
                              prom(internet_tx, "internet ↑")],
           w=24, h=9, stack=True, unit="bps", legend="table", calcs=("mean", "max", "lastNotNull"),
           overrides=[mirror("internet ↑"), color("internet ↓", "blue"),
                      color("IPTV ↓", "text"), color("internet ↑", "purple")],
           desc="Download above the axis, upload below. IPTV (VLAN 100) is split out of the wan0 counters."),
        ts("WAN traffic per day", [
            prom(f'clamp_min({day(RX, "wan0")} - {day(RX, "wan0.100")}, 0)', "internet ↓", interval="1d"),
            prom(f'clamp_min({day(TX, "wan0")} - {day(TX, "wan0.100")}, 0)', "internet ↑", interval="1d"),
            prom(day(RX, "wan0.100"), "IPTV ↓", interval="1d")],
           bars=True, unit="decbytes", timeFrom="30d",
           overrides=[color("internet ↓", "blue"), color("IPTV ↓", "text"), color("internet ↑", "purple")]),
        ts("WAN packets", [prom(rate("node_network_receive_packets_total", b("wan0")), "rx"),
                           prom(rate("node_network_transmit_packets_total", b("wan0")), "tx")],
           unit="pps", overrides=[mirror("tx")]),

        ("row", "LAN, IoT & IPTV"),
        ts("Bridges — to clients", [prom(bits(TX, f'{R},device=~"br-lan|br-iot"'), "{{device}}")],
           unit="bps", desc="Traffic the router sends into each bridge (mostly client downloads)."),
        ts("Bridges — from clients", [prom(bits(RX, f'{R},device=~"br-lan|br-iot"'), "{{device}}")],
           unit="bps", desc="Traffic received from each bridge (mostly client uploads). "
                            "Traffic switched between two ports of the same bridge is not counted here."),
        ts("Physical ports", [prom(bits(RX, f'{R},device=~"wan0|lan0|lan1"'), "{{device}} rx"),
                              prom(bits(TX, f'{R},device=~"wan0|lan0|lan1"'), "{{device}} tx")],
           unit="bps", overrides=[mirror(".* tx")], legend="table", calcs=("mean", "max"),
           desc="Wire-level counters, all VLANs included. tx drawn below the axis."),
        ts("VLAN sub-interfaces", [prom(bits(TX, f'{R},device=~"lan[01]\\\\.(30|100)"'), "{{device}} tx"),
                                   prom(bits(RX, f'{R},device=~"lan[01]\\\\.(30|100)"'), "{{device}} rx")],
           unit="bps", overrides=[mirror(".* rx")], legend="table", calcs=("mean", "max"),
           desc=".30 = IoT VLAN, .100 = IPTV VLAN. Above the axis: towards the device on that port."),
        ts("ARP neighbours", [prom(f'node_arp_entries{{{R},device=~"br-.*"}}', "{{device}}")], w=12, h=7),
        ts("Link flaps", [prom(f'increase(node_network_carrier_changes_total{{{ifaces},device!~"br-.*"}}[$__interval]) > 0',
                               "{{device}}")],
           w=12, h=7, bars=True, interval="5m",
           desc="Carrier changes per interval — a cable, port or Freebox reboot."),

        ("row", "DHCP leases"),
        table("Active leases", [prom(f"{lease()} - time() > 0", instant=True, table=True)],
              w=14, h=10, sort="Expires in", desc_sort=False,
              desc="Leases handed out by the networkd DHCP servers (dhcp-lease-metrics on the router). "
                   "Hostname is whatever the client sent; hosts reserved in config.toml are named "
                   "in the MAC column.",
              transformations=merge_table({"bridge": "Network", "hostname": "Hostname", "address": "Address",
                                           "mac": "MAC", "Value": "Expires in"},
                                          drop=("Time", "job", "instance", "__name__")),
              overrides=[
                  ovr("name", "Network", mappings=vmap({"br-lan": ("LAN", "green"), "br-iot": ("IoT", "orange")}),
                      custom__cellOptions={"type": "color-text"}),
                  ovr("name", "Hostname", mappings=[{"type": "special", "options": {
                      "match": "empty", "result": {"text": "—", "index": 0}}}]),
                  ovr("name", "MAC", mappings=f.mac_map()),
                  ovr("name", "Expires in", unit="dtdurations", decimals=0)]),
        stat("LAN leases", [prom(f'count({lease("br-lan")} > time())')], w=5, h=10,
             thresholds=steps("green", 0.8 * f.pools["br-lan"], "orange", f.pools["br-lan"], "red"),
             desc=f'Dynamic pool: {f.pools["br-lan"]} addresses (static reservations are counted too).'),
        stat("IoT leases", [prom(f'count({lease("br-iot")} > time())')], w=5, h=10,
             thresholds=steps("orange", 0.8 * f.pools["br-iot"], "red"),
             desc=f'Dynamic pool: {f.pools["br-iot"]} addresses (static reservations are counted too).'),
        ts("Active leases", [prom(f"count by (bridge) ({lease()} > time())", "{{bridge}}")],
           w=24, h=7, decimals=0, custom={"lineInterpolation": "stepAfter"},
           overrides=[color("br-lan", "green"), color("br-iot", "orange")]),

        ("row", "Interfaces"),
        table("Interfaces", [
            prom(f"sum by (device) (node_network_up{{{ifaces}}})", instant=True, table=True),
            prom(f"sum by (device) (node_network_speed_bytes{{{ifaces}}} * 8 > 0)", instant=True, table=True),
            prom(f"sum by (device) (node_network_mtu_bytes{{{ifaces}}})", instant=True, table=True),
            prom(f"sum by (device) ({bits(RX, ifaces)})", instant=True, table=True),
            prom(f"sum by (device) ({bits(TX, ifaces)})", instant=True, table=True),
            prom(f'sum by (device) (increase({{__name__=~"node_network_(receive|transmit)_(errs|drop)_total",{ifaces}}}[1d]))',
                 instant=True, table=True)],
            h=10, sort="device", desc_sort=False,
            transformations=merge_table({"device": "Interface", "Value #A": "State", "Value #B": "Speed",
                                         "Value #C": "MTU", "Value #D": "Receive", "Value #E": "Transmit",
                                         "Value #F": "Errors + drops (24 h)"}),
            overrides=[
                {"matcher": {"id": "byName", "options": "State"}, "properties": [
                    {"id": "mappings", "value": UP_MAP},
                    {"id": "custom.cellOptions", "value": {"type": "color-text"}}]},
                ovr("name", "Speed", unit="bps"),
                ovr("name", "Receive", unit="bps", decimals=1),
                ovr("name", "Transmit", unit="bps", decimals=1),
                {"matcher": {"id": "byName", "options": "Errors + drops (24 h)"}, "properties": [
                    {"id": "decimals", "value": 0},
                    {"id": "thresholds", "value": steps("green", 1, "orange", 100, "red")},
                    {"id": "custom.cellOptions", "value": {"type": "color-text"}}]}]),
        ts("Errors", [prom(f'{rate("node_network_receive_errs_total", ifaces)} > 0', "{{device}} rx"),
                      prom(f'{rate("node_network_transmit_errs_total", ifaces)} > 0', "{{device}} tx")],
           unit="pps", desc="Only interfaces with errors are shown."),
        ts("Drops", [prom(f'{rate("node_network_receive_drop_total", ifaces)} > 0', "{{device}} rx"),
                     prom(f'{rate("node_network_transmit_drop_total", ifaces)} > 0', "{{device}} tx")],
           unit="pps", desc="Only interfaces with drops are shown. Small rx drop rates on bridges are "
                            "frames for protocols nobody listens to."),
    ])


def access_points(f: Fleet) -> dict:
    A = 'job="aps",instance=~"${ap:regex}"'
    RX, TX = "node_network_receive_bytes_total", "node_network_transmit_bytes_total"
    trunk = f'{A},device="{f.trunk}"'
    vaps = f'{A},device=~"phy[0-9]+-ap[0-9]+"'
    st = lambda m: f.wifi(f"sum by (instance, ifname, mac) ({m}{{{A}}})")  # noqa: E731
    pct = steps("green", 70, "orange", 90, "red")
    signal_thr = steps("red", -75, "orange", -67, "green")
    return dashboard("home-aps", "Access Points", variables=[
        custom_var("ap", "AP", f.aps)], desc=(
        "OpenWrt access points (prometheus-node-exporter-lua): availability, "
        "Wi-Fi clients and signal, uplink traffic and system health."), panels=[
        stat("Status", [prom(f"up{{{A}}}", "{{instance}}")], w=6, h=5, graph=False,
             color_mode="background", text_mode="value_and_name", mappings=UP_MAP,
             thresholds=steps("red", 1, "green")),
        stat("Uptime", [prom(f"time() - node_boot_time_seconds{{{A}}}", "{{instance}}")], w=6, h=5,
             graph=False, unit="dtdurations", decimals=0, text_mode="value_and_name",
             thresholds=steps("blue")),
        stat("Wi-Fi clients", [prom(f"sum by (instance) (wifi_stations{{{A}}})", "{{instance}}")],
             w=6, h=5, text_mode="value_and_name", thresholds=steps("purple")),
        stat("Memory used", [prom(f"100 * (1 - node_memory_MemAvailable_bytes{{{A}}} / node_memory_MemTotal_bytes{{{A}}})",
                                  "{{instance}}")], w=6, h=5, unit="percent", decimals=0,
             text_mode="value_and_name", thresholds=pct),

        ("row", "Wi-Fi"),
        ts("Clients per AP", [prom(f"sum by (instance) (wifi_stations{{{A}}})", "{{instance}}")],
           stack=True, decimals=0, custom={"lineInterpolation": "stepAfter"}),
        ts("Clients per SSID & band", [prom(
            f'sum by (ssid, band) ({f.wifi(f"sum by (instance, ifname) (wifi_stations{{{A}}})")})',
            "{{ssid}} · {{band}}")],
           stack=True, decimals=0, custom={"lineInterpolation": "stepAfter"}),
        table("Connected stations", [
            prom(st("wifi_station_signal_dbm"), instant=True, table=True),
            prom(st("wifi_station_expected_throughput_kilobits_per_second") + " * 1000", instant=True, table=True),
            prom(st("wifi_station_receive_kilobits_per_second") + " * 1000", instant=True, table=True),
            prom(st("wifi_station_transmit_kilobits_per_second") + " * 1000", instant=True, table=True),
            prom(st("wifi_station_inactive_milliseconds") + " / 1000", instant=True, table=True)],
            h=9, sort="Signal",
            desc="RX/TX rate are the negotiated PHY bitrates of the last frame (AP side: TX = towards "
                 "the client), not traffic. Expected throughput is the driver's estimate of usable "
                 "throughput from the current rate and retries.",
            transformations=merge_table({"instance": "AP", "ssid": "SSID", "band": "Band", "mac": "Client",
                                         "Value #A": "Signal", "Value #B": "Expected throughput",
                                         "Value #C": "RX rate", "Value #D": "TX rate",
                                         "Value #E": "Inactive"}, drop=("Time", "job", "__name__", "ifname")),
            overrides=[
                {"matcher": {"id": "byName", "options": "Signal"}, "properties": [
                    {"id": "unit", "value": "dBm"}, {"id": "thresholds", "value": signal_thr},
                    {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}]},
                ovr("name", "Expected throughput", unit="bps"),
                ovr("name", "RX rate", unit="bps"), ovr("name", "TX rate", unit="bps"),
                ovr("name", "Inactive", unit="s", decimals=1),
                {"matcher": {"id": "byName", "options": "Client"},
                 "properties": [{"id": "mappings", "value": f.mac_map(upper=True)}]}]),
        ts("Client signal", [prom(f"sum by (instance, mac) (wifi_station_signal_dbm{{{A}}})", "{{mac}} @ {{instance}}")],
           unit="dBm", thresholds=signal_thr, legend="table", calcs=("min", "mean", "lastNotNull"),
           min=-100, max=-20, custom={"thresholdsStyle": {"mode": "dashed"}},
           desc="Below −75 dBm (red) expect retries and low rates; consider moving the client or an AP."),
        ts("Client expected throughput", [prom(
            f"sum by (instance, mac) (wifi_station_expected_throughput_kilobits_per_second{{{A}}}) * 1000",
            "{{mac}} @ {{instance}}")], unit="bps", legend="table", calcs=("min", "mean", "lastNotNull"),
           desc="Driver estimate of usable throughput to each client — drops with signal and retries."),

        ("row", "Traffic"),
        ts(f"Uplink ({f.trunk} port)", [prom(f"8 * {rate(RX, trunk)}", "{{instance}} ↓"),
                                        prom(f"8 * {rate(TX, trunk)}", "{{instance}} ↑")],
           unit="bps", overrides=[mirror(".*↑")], legend="table", calcs=("mean", "max"),
           desc="The trunk to the router (LAN untagged + IoT VLAN). ↓ = from the router, towards Wi-Fi clients."),
        ts("Wi-Fi traffic per SSID & band", [prom(
            f'sum by (instance, ssid, band) ({f.wifi(f"8 * sum by (instance, device) ({rate(TX, vaps)})", "device")})',
            "{{instance}} {{ssid}} · {{band}}")],
           unit="bps", legend="table", calcs=("mean", "max"),
           desc="Transmitted by each VAP, i.e. towards the clients."),

        ("row", "System"),
        ts("CPU busy", [prom(f'100 * (1 - avg by (instance) ({rate("node_cpu_seconds_total", A + ",mode=\"idle\"")}))',
                             "{{instance}}")], w=8, unit="percent", max=100),
        ts("Memory used", [prom(f"100 * (1 - node_memory_MemAvailable_bytes{{{A}}} / node_memory_MemTotal_bytes{{{A}}})",
                                "{{instance}}")], w=8, unit="percent", max=100),
        ts("Load (5m)", [prom(f"node_load5{{{A}}}", "{{instance}}")], w=8, decimals=2),
        timeline("Exporter status", [prom(f"up{{{A}}}", "{{instance}}")], mappings=UP_MAP,
                 thresholds=steps("red", 1, "green")),
        ts("Scrape duration", [prom(f"scrape_duration_seconds{{{A}}} and on (instance) up == 1", "{{instance}}")], h=6, unit="s"),
    ])


def logs(f: Fleet) -> dict:
    # Router entries come from journald (_HOSTNAME, SYSLOG_IDENTIFIER), AP
    # entries from RFC3164 syslog (hostname, app_name). Fold both into `host`
    # and `source` so every panel can treat them alike.
    base = ('* | format "<_HOSTNAME><hostname>" as host'
            ' | format "<SYSLOG_IDENTIFIER><app_name>" as source'
            ' | filter host:~"^(${host:regex})$" level:~"^(${level:regex})$" _msg:~"${search}"')
    severe = ' | filter level:in("emerg","alert","crit","error","warning")'
    wifi = (' | filter source:=hostapd _msg:~"AP-STA-(CONNECTED|DISCONNECTED)"'
            ' | extract_regexp "(?P<vap>[^ ]+): (?P<event>[A-Z-]+) (?P<client>[0-9a-f:]{17})"')
    level_colors = [color(lvl, c) for lvl, c in LEVEL_COLORS.items()]
    return dashboard("home-logs", "Logs", variables=[
        custom_var("host", "Host", ["home-router", *f.aps]),
        custom_var("level", "Level", LEVELS),
        {"type": "textbox", "name": "search", "label": "Regex", "query": "",
         "current": {"text": "", "value": ""}, "hide": 0}], desc=(
        "Router journal (via systemd-journal-upload) and AP syslog, both stored in VictoriaLogs."),
        time_from="now-24h", panels=[
        ts("Log volume by host", [logsq(base + " | stats by (host) count() lines", legendFormat="{{host}}")],
           bars=True, stack=True, h=7),
        ts("Log volume by level", [logsq(base + " | stats by (level) count() lines", legendFormat="{{level}}")],
           bars=True, stack=True, h=7, overrides=level_colors),
        ts("Errors & warnings by source", [logsq(base + severe + " | stats by (host, source) count() lines",
                                                 legendFormat="{{host}} · {{source}}")],
           bars=True, stack=True, h=8, legend="table", calcs=("sum",)),
        table("Top sources", [logsq(base + " | stats by (host, source) count() lines", "stats")],
              w=12, h=8, sort="Lines",
              transformations=[{"id": "reduce", "options": {"reducers": ["lastNotNull"], "labelsToFields": True}},
                               {"id": "organize", "options": {
                                   "excludeByName": {"Field": True, "__name__": True},
                                   "renameByName": {"host": "Host", "source": "Source", "Last *": "Lines",
                                                    "Last (not null)": "Lines"},
                                   "indexByName": {"host": 0, "source": 1}}}]),
        ts("Wi-Fi associations", [logsq(base + wifi + " | stats by (host, event) count() events",
                                        legendFormat="{{host}} {{event}}")],
           bars=True, stack=True, h=8, legend="table", calcs=("sum",),
           desc="hostapd AP-STA-CONNECTED / AP-STA-DISCONNECTED events. A steady stream of both for "
                "one AP usually means a client roaming back and forth or a flaky link."),
        logs_panel("Wi-Fi association log", base + wifi
                   + " | format \"<event> <client> on <vap>\" as _msg", w=12, h=8, max_lines=200),

        ("row", "Logs"),
        logs_panel("Errors & warnings", base + severe, h=10, max_lines=500),
        logs_panel("All logs", base, h=18),
    ])


DASHBOARDS = (router_overview, router_network, access_points, logs)


# --------------------------------------------------------------------------
# Grafana API
# --------------------------------------------------------------------------

class Grafana:
    def __init__(self) -> None:
        self.url = os.environ.get("GRAFANA_URL", "http://nas.lan:30037").rstrip("/")
        token = os.environ.get("GRAFANA_TOKEN")
        if not token:
            path = Path.home() / ".config" / "grafana-token"
            if not path.is_file():
                raise SystemExit(f"error: set GRAFANA_TOKEN or create {path}")
            token = path.read_text().strip()
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.url + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"error: {method} {path}: {exc.code} {exc.read().decode()}") from exc

    def ensure_folder(self) -> None:
        folders = self.call("GET", "/api/folders")
        if not any(fo["uid"] == FOLDER_UID for fo in folders):
            self.call("POST", "/api/folders", {"uid": FOLDER_UID, "title": FOLDER_TITLE})

    def push(self, dash: dict) -> str:
        res = self.call("POST", "/api/dashboards/db", {
            "dashboard": dash, "folderUid": FOLDER_UID, "overwrite": True,
            "message": "monitoring/grafana/build.py"})
        return self.url + res["url"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, help="write dashboard JSON files into this directory")
    parser.add_argument("--push", action="store_true", help="create/overwrite the dashboards in Grafana")
    args = parser.parse_args()
    if not (args.out or args.push):
        parser.error("nothing to do: pass --out DIR and/or --push")

    try:
        fleet = Fleet()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    dashes = [build(fleet) for build in DASHBOARDS]

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for d in dashes:
            (args.out / f"{d['uid']}.json").write_text(json.dumps(d, indent=2) + "\n")
    if args.push:
        grafana = Grafana()
        grafana.ensure_folder()
        for d in dashes:
            print(f"{d['title']}: {grafana.push(d)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
