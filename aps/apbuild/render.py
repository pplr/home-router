"""Generate UCI config + rewrite /etc/shadow + install dropbear host keys.

All transforms are pure Python — no shell anywhere, sidestepping the
router's extrausers ``$``-eating trap (see CLAUDE.md "useradd -p hash
dollars"). The three /etc/config/* files (system, network, wireless)
are generated from FleetConfig + APSpec by composing UCI text in
f-strings; the remaining files (shadow, authorized_keys, host keys)
are baked verbatim from secrets/.
"""

from __future__ import annotations

import os

from .config import APSpec, CommonConfig, Radio, Ssid
from .secrets import APTlsSecrets, Secrets


class RenderError(Exception):
    """Shadow file malformed, etc."""


def render_all(
    spec: APSpec,
    common: CommonConfig,
    secrets: Secrets,
    tls: APTlsSecrets | None = None,
) -> None:
    """Run every generator + secret installer on the staged files directory.

    Stage.merge() must have run first so common/files overlays are in
    place; the generators write fresh /etc/config/* on top of (or
    instead of) anything common/files happens to ship there.

    ``tls`` is None for APs not opted into the certificate flow; those
    get no TLS material and a plain-HTTP uhttpd.
    """

    _generate_and_write_system(spec, common)
    _generate_and_write_network(spec, common)
    _generate_and_write_wireless(spec, common, secrets)
    _rewrite_shadow(spec, secrets)
    _install_authorized_keys(spec, secrets, tls)
    _install_dropbear_host_keys(spec, secrets)
    _generate_and_write_node_exporter(spec)
    _generate_and_write_uhttpd(spec, tls)
    _install_tls_material(spec, tls)


# ---- /etc/config/system -----------------------------------------------


def _generate_and_write_system(spec: APSpec, common: CommonConfig) -> None:
    out_path = spec.staged_files_dir / "etc" / "config" / "system"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_generate_system(spec, common), encoding="utf-8")
    os.chmod(out_path, 0o644)


def _generate_system(spec: APSpec, common: CommonConfig) -> str:
    sl = common.syslog
    return (
        f"config system\n"
        f"\toption hostname '{spec.hostname}'\n"
        f"\toption timezone '{common.timezone}'\n"
        f"\toption zonename '{common.zonename}'\n"
        f"\t# Remote syslog to the router's rsyslog collector on br-lan.\n"
        f"\toption log_ip '{sl.ip}'\n"
        f"\toption log_port '{sl.port}'\n"
        f"\toption log_proto '{sl.proto}'\n"
        f"\toption log_hostname '{spec.hostname}'\n"
        f"\n"
        f"config timeserver 'ntp'\n"
        f"\toption enabled '1'\n"
        f"\toption enable_server '0'\n"
        f"\tlist server '{common.router.ipv4}'\n"
    )


# ---- /etc/config/network ----------------------------------------------


def _generate_and_write_network(spec: APSpec, common: CommonConfig) -> None:
    out_path = spec.staged_files_dir / "etc" / "config" / "network"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_generate_network(spec, common), encoding="utf-8")
    os.chmod(out_path, 0o644)


def _generate_network(spec: APSpec, common: CommonConfig) -> str:
    port = common.uplink.trunk_port_alias
    vlan = common.uplink.iot_vlan
    return (
        f"config interface 'loopback'\n"
        f"\toption device 'lo'\n"
        f"\toption proto 'static'\n"
        f"\toption ipaddr '127.0.0.1'\n"
        f"\toption netmask '255.0.0.0'\n"
        f"\n"
        f"config globals 'globals'\n"
        f"\toption ula_prefix 'auto'\n"
        f"\n"
        f"# Trusted management bridge: untagged frames on the {port} port land here,\n"
        f"# joined by every wifi-iface with `option network 'lan'`. Static IP so the\n"
        f"# router-side DHCP server has nothing to lease for the AP itself.\n"
        f"config device\n"
        f"\toption name 'br-lan'\n"
        f"\toption type 'bridge'\n"
        f"\tlist ports '{port}'\n"
        f"\n"
        f"config interface 'lan'\n"
        f"\toption device 'br-lan'\n"
        f"\toption proto 'static'\n"
        f"\toption ipaddr '{spec.management_ip}'\n"
        f"\toption netmask '255.255.255.0'\n"
        f"\toption gateway '{common.router.ipv4}'\n"
        f"\tlist dns '{common.router.ipv4}'\n"
        f"\t# IPv6 stays at stock-default SLAAC: the AP picks up a GUA from the\n"
        f"\t# router's RA on br-lan ({common.router.lan_v6_prefix}) without any\n"
        f"\t# extra config here.\n"
        f"\n"
        f"# IoT bridge: tag {vlan} on the {port} trunk, bridged with the IoT VAPs.\n"
        f"# L2 only — the router holds the L3/RA/DHCP for the IoT subnet.\n"
        f"config device\n"
        f"\toption name 'br-iot'\n"
        f"\toption type 'bridge'\n"
        f"\tlist ports '{port}.{vlan}'\n"
        f"\n"
        f"config interface 'iot'\n"
        f"\toption device 'br-iot'\n"
        f"\toption proto 'none'\n"
    )


# ---- /etc/config/wireless ---------------------------------------------


def _generate_and_write_wireless(
    spec: APSpec, common: CommonConfig, secrets: Secrets
) -> None:
    out_path = spec.staged_files_dir / "etc" / "config" / "wireless"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_generate_wireless(spec, common, secrets), encoding="utf-8")
    # /etc/config/wireless contains PSKs in cleartext — restrict mode.
    os.chmod(out_path, 0o600)


def _generate_wireless(spec: APSpec, common: CommonConfig, secrets: Secrets) -> str:
    """Compose UCI: all wifi-device entries, then SSID-major VAP entries.

    SSID-major ordering (every band of `trusted` before every band of
    `iot`) keeps related VAPs adjacent in the file, matching the layout
    a hand-written wireless config would use.
    """

    parts: list[str] = []

    for radio in spec.radios:
        parts.append(_radio_block(radio, common))

    for ssid in common.ssids:
        psk = secrets.psks[ssid.psk_secret]
        for radio in spec.radios:
            if radio.disabled:
                continue
            parts.append(_vap_block(radio, ssid, psk))

    return "\n".join(parts) + "\n"


def _radio_block(radio: Radio, common: CommonConfig) -> str:
    if radio.disabled:
        return (
            f"config wifi-device 'radio{radio.index}'\n"
            f"\toption type 'mac80211'\n"
            f"\toption path '{radio.path}'\n"
            f"\toption disabled '1'\n"
        )
    return (
        f"config wifi-device 'radio{radio.index}'\n"
        f"\toption type 'mac80211'\n"
        f"\toption path '{radio.path}'\n"
        f"\toption band '{radio.band}'\n"
        f"\toption htmode '{radio.htmode}'\n"
        f"\toption channel '{radio.channel}'\n"
        f"\toption country '{common.country}'\n"
        f"\toption cell_density '0'\n"
    )


def _vap_block(radio: Radio, ssid: Ssid, psk: str) -> str:
    # Section name <ssid-key>_<band> guarantees uniqueness across the
    # same SSID on multiple radios (wifinet_trusted_2g vs _5g) and
    # keeps the file readable.
    name = f"wifinet_{ssid.key}_{radio.band}"
    return (
        f"config wifi-iface '{name}'\n"
        f"\toption device 'radio{radio.index}'\n"
        f"\toption mode 'ap'\n"
        f"\toption network '{ssid.network}'\n"
        f"\toption ssid '{ssid.name}'\n"
        f"\toption encryption '{ssid.encryption}'\n"
        f"\toption ieee80211w '{ssid.ieee80211w}'\n"
        f"\toption key '{psk}'\n"
    )


# ---- /etc/shadow -------------------------------------------------------


def _rewrite_shadow(spec: APSpec, secrets: Secrets) -> None:
    """Rewrite the ``root:`` line's hash field with ``secrets/root.hash``.

    If ``/etc/shadow`` is not present in the staged overlay, create one
    with just the root line — OpenWrt's Image Builder will merge that
    onto the rootfs (overlay files mask their /rom counterparts).
    """

    shadow_path = spec.staged_files_dir / "etc" / "shadow"
    if shadow_path.exists():
        lines = shadow_path.read_text(encoding="utf-8").splitlines()
        new_lines: list[str] = []
        found = False
        for line in lines:
            if line.startswith("root:"):
                fields = line.split(":")
                if len(fields) < 9:
                    raise RenderError(
                        f"Malformed root line in {shadow_path}: {line!r}"
                    )
                fields[1] = secrets.root_password_hash
                new_lines.append(":".join(fields))
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.insert(0, _new_root_shadow_line(secrets.root_password_hash))
        shadow_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        shadow_path.parent.mkdir(parents=True, exist_ok=True)
        shadow_path.write_text(
            _new_root_shadow_line(secrets.root_password_hash) + "\n",
            encoding="utf-8",
        )
    os.chmod(shadow_path, 0o600)


def _new_root_shadow_line(password_hash: str) -> str:
    """Minimal valid shadow(5) entry for ``root`` with no aging policy."""

    return f"root:{password_hash}:0:0:99999:7:::"


# ---- /etc/dropbear/authorized_keys ------------------------------------


def _install_authorized_keys(
    spec: APSpec, secrets: Secrets, tls: APTlsSecrets | None
) -> None:
    """Install the admin keys, plus the router's restricted push key.

    The push key is confined to a forced command: dropbear ignores
    whatever the client asks to run and executes ``accept-ap-cert.sh``
    instead, which reads a certificate on stdin and exits. Combined with
    the no-* restrictions, a leaked push key yields no shell, no port
    forwarding, and no way to touch anything but /etc/uhttpd/ap.crt.

    Only added when the AP is in the certificate flow — an AP with no
    TLS material has nothing for the router to push.
    """

    content = secrets.authorized_keys
    if tls is not None:
        if not content.endswith("\n"):
            content += "\n"
        content += (
            "# Router certificate-push key — restricted to a forced command.\n"
            "# Generated by apbuild.render; see the futro-ap-certs recipe.\n"
            'command="/usr/libexec/accept-ap-cert.sh",no-pty,no-agent-forwarding,'
            "no-port-forwarding,no-X11-forwarding "
            f"{secrets.ap_push_pubkey.strip()}\n"
        )

    out_path = spec.staged_files_dir / "etc" / "dropbear" / "authorized_keys"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    os.chmod(out_path, 0o600)


# ---- /etc/dropbear/dropbear_{ed25519,rsa}_host_key --------------------


def _install_dropbear_host_keys(spec: APSpec, secrets: Secrets) -> None:
    """Bake both dropbear host key types so first-boot regen is a no-op.

    Without this, dropbear regenerates host keys on first boot of every
    fresh sysupgrade — same fingerprint-flip trap the router hit with
    sshdgenkeys (see CLAUDE.md, "Bake *all* host key types").
    """

    dropbear_dir = spec.staged_files_dir / "etc" / "dropbear"
    dropbear_dir.mkdir(parents=True, exist_ok=True)
    for name, blob in (
        ("dropbear_ed25519_host_key", secrets.dropbear_ed25519_host_key),
        ("dropbear_rsa_host_key", secrets.dropbear_rsa_host_key),
    ):
        path = dropbear_dir / name
        path.write_bytes(blob)
        os.chmod(path, 0o600)


# ---- /etc/config/prometheus-node-exporter-lua -------------------------


def _generate_and_write_node_exporter(spec: APSpec) -> None:
    """Expose this AP's metrics for the router's netdata to scrape.

    Every AP runs prometheus-node-exporter-lua (pulled in via
    common.packages); the router's netdata go.d/prometheus collector
    scrapes http://<ap>:9100/metrics. ``listen_interface 'lan'`` binds the
    exporter to the br-lan address only (not 0.0.0.0), keeping it off the
    IoT/IPTV bridges. No secret involved — the scrape is unauthenticated
    on the trusted LAN, same posture as the router's own netdata.
    """

    out_path = (
        spec.staged_files_dir
        / "etc"
        / "config"
        / "prometheus-node-exporter-lua"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f"config prometheus-node-exporter-lua 'main'\n"
        f"\toption listen_interface 'lan'\n"
        f"\toption listen_ipv6 '0'\n"
        f"\toption listen_port '9100'\n",
        encoding="utf-8",
    )
    os.chmod(out_path, 0o644)


# ---- /etc/config/uhttpd -----------------------------------------------

# Where the baked key and the (bootstrap, later Let's Encrypt) cert live.
# accept-ap-cert.sh rewrites CERT_PATH in place when the router pushes a
# renewal, so these paths are part of the AP<->router contract.
KEY_PATH = "/etc/uhttpd/ap.key"
CERT_PATH = "/etc/uhttpd/ap.crt"


def _generate_and_write_uhttpd(spec: APSpec, tls: APTlsSecrets | None) -> None:
    out_path = spec.staged_files_dir / "etc" / "config" / "uhttpd"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_generate_uhttpd(spec, tls), encoding="utf-8")
    os.chmod(out_path, 0o644)


def _generate_uhttpd(spec: APSpec, tls: APTlsSecrets | None) -> str:
    """Compose /etc/config/uhttpd for LuCI.

    Listeners are bound to the AP's management address rather than
    0.0.0.0 — the APs run no firewall (firewall4 is removed fleet-wide in
    config.toml), so binding is the isolation mechanism that keeps LuCI
    off br-iot. Same posture as prometheus-node-exporter-lua's
    ``listen_interface 'lan'``.

    IPv6 is deliberately not bound: the AP's GUA comes from the router's
    RA and isn't known at build time, and the certificate FQDN resolves
    to the IPv4 management address anyway.
    """

    ip = spec.management_ip
    head = (
        f"# Generated by apbuild.render — do not edit on the device.\n"
        f"# Bound to {ip} only: the APs run no firewall, so the listen\n"
        f"# address is what keeps LuCI off the untrusted IoT bridge.\n"
        f"\n"
        f"config uhttpd 'main'\n"
        f"\tlist listen_http '{ip}:80'\n"
    )

    if tls is None:
        # No certificate flow for this AP — plain HTTP only.
        body = ""
    else:
        body = (
            f"\tlist listen_https '{ip}:443'\n"
            f"\toption redirect_https '1'\n"
            f"\t# Baked key (never leaves this device) + certificate issued by\n"
            f"\t# the router via DNS-01 for {spec.cert_fqdn}. Until the first\n"
            f"\t# push lands, the cert is the self-signed bootstrap one.\n"
            f"\toption cert '{CERT_PATH}'\n"
            f"\toption key '{KEY_PATH}'\n"
        )

    tail = (
        f"\toption home '/www'\n"
        f"\toption rfc1918_filter '1'\n"
        f"\toption max_requests '3'\n"
        f"\toption max_connections '100'\n"
        f"\toption cgi_prefix '/cgi-bin'\n"
        f"\toption script_timeout '60'\n"
        f"\toption network_timeout '30'\n"
        f"\toption http_keepalive '20'\n"
        f"\toption tcp_keepalive '1'\n"
        f"\toption ubus_prefix '/ubus'\n"
    )
    return head + body + tail


# ---- /etc/uhttpd/ap.{key,crt} -----------------------------------------


def _install_tls_material(spec: APSpec, tls: APTlsSecrets | None) -> None:
    """Bake the AP's private key and its bootstrap certificate.

    The key is generated once on the build host and baked only into this
    AP's image; the router never receives it (it signs a CSR instead).
    Baking it means the identity survives ``sysupgrade -n``, which wipes
    the overlay — the trade-off being that the key rotates on reflash
    rather than on every renewal.
    """

    if tls is None:
        return

    uhttpd_dir = spec.staged_files_dir / "etc" / "uhttpd"
    uhttpd_dir.mkdir(parents=True, exist_ok=True)

    key_path = uhttpd_dir / "ap.key"
    key_path.write_text(tls.key, encoding="utf-8")
    os.chmod(key_path, 0o600)

    cert_path = uhttpd_dir / "ap.crt"
    cert_path.write_text(tls.bootstrap_cert, encoding="utf-8")
    os.chmod(cert_path, 0o644)
