"""Parse and validate ``hosts.toml`` — the home-network single source of truth.

Strict: unknown keys raise ``ConfigError`` with the offending section
path. IPs must fall in the right VLAN's subnet AND outside the dynamic
DHCP pool. MACs must be lowercase canonical. Names/aliases must be
unique across the whole file. Typos in config fail fast at build time
rather than silently no-op at first boot.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Malformed or missing hosts config."""


# ---- dataclasses (all frozen — mutation = bug) -------------------------


@dataclass(frozen=True)
class DhcpRange:
    """Dynamic-pool bounds for one VLAN's DHCP server."""

    pool_offset: int
    pool_size: int
    lease_seconds: int


@dataclass(frozen=True)
class RouterConfig:
    lan_subnet: ipaddress.IPv4Network
    lan_ipv4: ipaddress.IPv4Address
    iot_subnet: ipaddress.IPv4Network
    iot_ipv4: ipaddress.IPv4Address
    dns_search_domain: str
    dhcp_lan: DhcpRange
    dhcp_iot: DhcpRange


@dataclass(frozen=True)
class Host:
    """One device on the home network.

    ``mac`` is optional: hosts without a MAC get only ``/etc/hosts`` +
    DNS entries (no DHCP reservation). Use this for devices that own
    their own static-IP config (e.g. an OpenWrt AP).
    """

    name: str
    ipv4: ipaddress.IPv4Address
    vlan: str  # "lan" | "iot"
    mac: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class HostsConfig:
    router: RouterConfig
    hosts: tuple[Host, ...]

    @classmethod
    def load(cls, path: Path) -> "HostsConfig":
        if not path.is_file():
            raise ConfigError(f"hosts.toml not found: {path}")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: TOML parse error: {exc}") from exc

        _check_keys(data, {"router", "hosts"}, f"{path} (top level)")
        router = _parse_router(_require_section(data, "router", path))

        hosts_raw = data.get("hosts", [])
        if not isinstance(hosts_raw, list):
            raise ConfigError(f"{path}: [[hosts]] must be an array of tables")
        hosts = tuple(_parse_host(i, body, router) for i, body in enumerate(hosts_raw))

        _check_uniqueness(hosts)
        return cls(router=router, hosts=hosts)

    def hosts_by_vlan(self, vlan: str) -> tuple[Host, ...]:
        return tuple(h for h in self.hosts if h.vlan == vlan)

    def host_by_name(self, name: str) -> Host:
        for h in self.hosts:
            if h.name == name:
                return h
        raise ConfigError(f"No [[hosts]] entry with name = {name!r}")


# ---- validators --------------------------------------------------------


_MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_VLANS = frozenset({"lan", "iot"})


def _parse_router(d: dict) -> RouterConfig:
    section = "[router]"
    _check_keys(
        d,
        {
            "lan_subnet", "lan_ipv4", "iot_subnet", "iot_ipv4",
            "dns_search_domain", "dhcp",
        },
        section,
    )
    lan_subnet = _parse_ipv4_network(_req_str(d, "lan_subnet", section), f"{section}.lan_subnet")
    lan_ipv4 = _parse_ipv4(_req_str(d, "lan_ipv4", section), f"{section}.lan_ipv4")
    iot_subnet = _parse_ipv4_network(_req_str(d, "iot_subnet", section), f"{section}.iot_subnet")
    iot_ipv4 = _parse_ipv4(_req_str(d, "iot_ipv4", section), f"{section}.iot_ipv4")
    if lan_ipv4 not in lan_subnet:
        raise ConfigError(f"{section}.lan_ipv4 ({lan_ipv4}) not in lan_subnet ({lan_subnet})")
    if iot_ipv4 not in iot_subnet:
        raise ConfigError(f"{section}.iot_ipv4 ({iot_ipv4}) not in iot_subnet ({iot_subnet})")

    domain = _req_str(d, "dns_search_domain", section)
    if not _NAME_RE.match(domain):
        raise ConfigError(
            f"{section}.dns_search_domain {domain!r} must be a single DNS label"
            " (lowercase letters/digits/hyphen, starting with a letter)"
        )

    dhcp = _require_section(d, "dhcp", section)
    _check_keys(dhcp, {"lan", "iot"}, f"{section}.dhcp")
    return RouterConfig(
        lan_subnet=lan_subnet,
        lan_ipv4=lan_ipv4,
        iot_subnet=iot_subnet,
        iot_ipv4=iot_ipv4,
        dns_search_domain=domain,
        dhcp_lan=_parse_dhcp_range(_require_section(dhcp, "lan", f"{section}.dhcp"), f"{section}.dhcp.lan", lan_subnet),
        dhcp_iot=_parse_dhcp_range(_require_section(dhcp, "iot", f"{section}.dhcp"), f"{section}.dhcp.iot", iot_subnet),
    )


def _parse_dhcp_range(d: dict, section: str, subnet: ipaddress.IPv4Network) -> DhcpRange:
    _check_keys(d, {"pool_offset", "pool_size", "lease_seconds"}, section)
    offset = _req_int(d, "pool_offset", section)
    size = _req_int(d, "pool_size", section)
    lease = _req_int(d, "lease_seconds", section)
    # Networkd treats pool_offset as the host index inside the subnet
    # (i.e. .100 for offset=100 in a /24). Validate bounds.
    if offset < 1:
        raise ConfigError(f"{section}.pool_offset must be ≥ 1")
    if size < 1:
        raise ConfigError(f"{section}.pool_size must be ≥ 1")
    # 0-based host count = subnet.num_addresses - 2 (network + broadcast).
    max_host_idx = subnet.num_addresses - 2
    if offset + size - 1 > max_host_idx:
        raise ConfigError(
            f"{section}: pool [{offset}..{offset + size - 1}] exceeds subnet host range"
            f" (max index {max_host_idx} for {subnet})"
        )
    if lease < 60:
        raise ConfigError(f"{section}.lease_seconds must be ≥ 60")
    return DhcpRange(pool_offset=offset, pool_size=size, lease_seconds=lease)


def _parse_host(index: int, d: object, router: RouterConfig) -> Host:
    section = f"[[hosts]] (index {index})"
    if not isinstance(d, dict):
        raise ConfigError(f"{section} must be a table")
    _check_keys(d, {"name", "ipv4", "vlan", "mac", "aliases"}, section)

    name = _req_str(d, "name", section)
    if not _NAME_RE.match(name):
        raise ConfigError(
            f"{section}.name {name!r} must be lowercase letters/digits/hyphen,"
            " starting with a letter (DNS-label safe)"
        )

    vlan = _req_str(d, "vlan", section)
    if vlan not in _VLANS:
        raise ConfigError(f"{section}.vlan must be one of {sorted(_VLANS)}, got {vlan!r}")

    ipv4 = _parse_ipv4(_req_str(d, "ipv4", section), f"{section}.ipv4")
    subnet = router.lan_subnet if vlan == "lan" else router.iot_subnet
    if ipv4 not in subnet:
        raise ConfigError(
            f"{section}.ipv4 ({ipv4}) outside the {vlan} subnet ({subnet})"
        )
    dhcp_range = router.dhcp_lan if vlan == "lan" else router.dhcp_iot
    host_idx = int(ipv4) - int(subnet.network_address)
    pool_lo, pool_hi = dhcp_range.pool_offset, dhcp_range.pool_offset + dhcp_range.pool_size - 1
    if pool_lo <= host_idx <= pool_hi:
        raise ConfigError(
            f"{section}.ipv4 ({ipv4}) falls inside the {vlan} dynamic DHCP pool"
            f" [.{pool_lo}..{pool_hi}] — pick an address outside the pool so the"
            " server can't hand it out to a different MAC"
        )

    mac: str | None = None
    if "mac" in d:
        raw_mac = _req_str(d, "mac", section)
        if not _MAC_RE.match(raw_mac):
            raise ConfigError(
                f"{section}.mac {raw_mac!r} must be lowercase canonical"
                " (aa:bb:cc:dd:ee:ff)"
            )
        mac = raw_mac

    aliases: tuple[str, ...] = ()
    if "aliases" in d:
        raw = _req_str_list(d, "aliases", section, allow_empty=True)
        for a in raw:
            if not _NAME_RE.match(a):
                raise ConfigError(
                    f"{section}.aliases: {a!r} must be a DNS-label-safe lowercase identifier"
                )
        aliases = tuple(raw)

    return Host(name=name, ipv4=ipv4, vlan=vlan, mac=mac, aliases=aliases)


def _check_uniqueness(hosts: tuple[Host, ...]) -> None:
    seen_names: dict[str, str] = {}
    seen_ips: dict[ipaddress.IPv4Address, str] = {}
    seen_macs: dict[str, str] = {}
    for h in hosts:
        for label in (h.name, *h.aliases):
            if label in seen_names:
                raise ConfigError(
                    f"name/alias {label!r} declared twice"
                    f" (first by {seen_names[label]!r}, again by {h.name!r})"
                )
            seen_names[label] = h.name
        if h.ipv4 in seen_ips:
            raise ConfigError(
                f"IPv4 {h.ipv4} declared twice"
                f" (first by {seen_ips[h.ipv4]!r}, again by {h.name!r})"
            )
        seen_ips[h.ipv4] = h.name
        if h.mac is not None:
            if h.mac in seen_macs:
                raise ConfigError(
                    f"MAC {h.mac} declared twice"
                    f" (first by {seen_macs[h.mac]!r}, again by {h.name!r})"
                )
            seen_macs[h.mac] = h.name


# ---- helpers -----------------------------------------------------------


def _require_section(d: dict, key: str, source: object) -> dict:
    if key not in d:
        raise ConfigError(f"{source}: missing required section [{key}]")
    v = d[key]
    if not isinstance(v, dict):
        raise ConfigError(f"{source}: [{key}] must be a table")
    return v


def _check_keys(d: dict, allowed: set[str], section: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise ConfigError(f"{section}: unknown keys: {sorted(extra)}")


def _req_str(d: dict, key: str, section: str) -> str:
    if key not in d:
        raise ConfigError(f"{section} missing required key {key!r}")
    v = d[key]
    if not isinstance(v, str):
        raise ConfigError(f"{section}.{key} must be string, got {type(v).__name__}")
    if not v:
        raise ConfigError(f"{section}.{key} must not be empty")
    return v


def _req_int(d: dict, key: str, section: str) -> int:
    if key not in d:
        raise ConfigError(f"{section} missing required key {key!r}")
    v = d[key]
    if not isinstance(v, int) or isinstance(v, bool):
        raise ConfigError(f"{section}.{key} must be int, got {type(v).__name__}")
    return v


def _req_str_list(d: dict, key: str, section: str, *, allow_empty: bool = False) -> list[str]:
    v = d[key]
    if not isinstance(v, list):
        raise ConfigError(f"{section}.{key} must be array, got {type(v).__name__}")
    if not allow_empty and not v:
        raise ConfigError(f"{section}.{key} must not be empty")
    for i, item in enumerate(v):
        if not isinstance(item, str):
            raise ConfigError(
                f"{section}.{key}[{i}] must be string, got {type(item).__name__}"
            )
    return v


def _parse_ipv4(value: str, source: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(value)
    except (ValueError, ipaddress.AddressValueError) as exc:
        raise ConfigError(f"{source} not a valid IPv4 address: {value!r}") from exc


def _parse_ipv4_network(value: str, source: str) -> ipaddress.IPv4Network:
    try:
        return ipaddress.IPv4Network(value, strict=True)
    except (ValueError, ipaddress.NetmaskValueError) as exc:
        raise ConfigError(f"{source} not a valid IPv4 network: {value!r}") from exc
