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
from .secrets import Secrets


class RenderError(Exception):
    """Shadow file malformed, etc."""


def render_all(spec: APSpec, common: CommonConfig, secrets: Secrets) -> None:
    """Run every generator + secret installer on the staged files directory.

    Stage.merge() must have run first so common/files overlays are in
    place; the generators write fresh /etc/config/* on top of (or
    instead of) anything common/files happens to ship there.
    """

    _generate_and_write_system(spec, common)
    _generate_and_write_network(spec, common)
    _generate_and_write_wireless(spec, common, secrets)
    _rewrite_shadow(spec, secrets)
    _install_authorized_keys(spec, secrets)
    _install_dropbear_host_keys(spec, secrets)


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


def _install_authorized_keys(spec: APSpec, secrets: Secrets) -> None:
    out_path = spec.staged_files_dir / "etc" / "dropbear" / "authorized_keys"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(secrets.authorized_keys, encoding="utf-8")
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
