"""Parse and validate ``config.toml`` — the home-network single source of truth.

Covers the whole network: ``[router]``, ``[acme]``, ``[[hosts]]`` for
ordinary devices, and one ``[aps.<label>]`` table per OpenWrt access
point. Access points are declared *once*, here — identity and build spec
together.

This module owns the slice the **router** build needs: everything except
the AP build spec (target/profile/radios/…), which ``aps/apbuild``
parses from the same file. That split exists because ``aps/apbuild``
imports this package, so this package must not import back. The per-AP
key check still *allow-lists* the build-spec keys, so a typo there fails
here too rather than being silently ignored.

Strict throughout: unknown keys raise ``ConfigError`` with the offending
section path. IPs must fall in the right VLAN's subnet AND outside the
dynamic DHCP pool. MACs must be lowercase canonical. Names, labels and
aliases must be unique across hosts *and* APs. Typos fail fast at build
time rather than silently no-op at first boot.
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
    # The /64 delegated to br-lan. Referenced in the APs' generated
    # /etc/config/network; they pick up a GUA from the router's RA.
    lan_v6_prefix: str


@dataclass(frozen=True)
class AcmeConfig:
    """Zone + contact for the APs' centrally-issued LuCI certificates.

    Only the ephemeral ``_acme-challenge`` TXT records ever reach the
    public zone — the A records are local-only, served from the
    generated ``/etc/hosts``. Absent (``None``) when no AP sets
    ``cert = true``.
    """

    domain: str
    email: str


@dataclass(frozen=True)
class Host:
    """One non-AP device on the home network.

    ``mac`` is optional: hosts without a MAC get only ``/etc/hosts`` +
    DNS entries (no DHCP reservation). Use that for devices that own
    their own static-IP config.

    Access points are *not* hosts — they live in ``[aps.<label>]`` and
    are modelled by :class:`AP`.
    """

    name: str
    ipv4: ipaddress.IPv4Address
    vlan: str  # "lan" | "iot"
    mac: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class AP:
    """One OpenWrt access point, from its ``[aps.<label>]`` table.

    ``label`` is the table key (e.g. ``ax59u``) and is the AP's single
    identifier: the short ``/etc/hosts`` alias, the leftmost label of its
    certificate FQDN, its secrets directory under ``aps/secrets/``, and
    the ``./aps/build.py <label>`` argument.

    APs always sit on the trusted ``lan`` bridge and always own their IP
    statically, so there is no ``vlan`` or ``mac`` field.

    ``cert`` opts the AP into the centralised ACME flow: the router
    issues a Let's Encrypt certificate for ``<label>.<acme.domain>`` from
    a CSR baked into the AP's own image, then pushes the signed
    certificate back. The private key never leaves the AP.
    """

    label: str
    hostname: str
    ipv4: ipaddress.IPv4Address
    cert: bool = False
    aliases: tuple[str, ...] = ()

    def cert_fqdn(self, acme: AcmeConfig | None) -> str:
        if acme is None:
            raise ConfigError(
                f"AP {self.label!r} sets cert = true but [acme] is not configured"
            )
        return f"{self.label}.{acme.domain}"


@dataclass(frozen=True)
class NetworkConfig:
    router: RouterConfig
    hosts: tuple[Host, ...]
    aps: tuple[AP, ...]
    acme: AcmeConfig | None = None

    @classmethod
    def load(cls, path: Path) -> "NetworkConfig":
        if not path.is_file():
            raise ConfigError(f"config.toml not found: {path}")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: TOML parse error: {exc}") from exc

        _check_keys(data, {"router", "acme", "hosts", "aps"}, f"{path} (top level)")
        router = _parse_router(_require_section(data, "router", path))

        acme: AcmeConfig | None = None
        if "acme" in data:
            acme = _parse_acme(_require_section(data, "acme", path))

        hosts_raw = data.get("hosts", [])
        if not isinstance(hosts_raw, list):
            raise ConfigError(f"{path}: [[hosts]] must be an array of tables")
        hosts = tuple(_parse_host(i, body, router) for i, body in enumerate(hosts_raw))

        aps = _parse_aps(data.get("aps", {}), router, acme)

        _check_uniqueness(hosts, aps)
        return cls(router=router, hosts=hosts, aps=aps, acme=acme)

    def hosts_by_vlan(self, vlan: str) -> tuple[Host, ...]:
        return tuple(h for h in self.hosts if h.vlan == vlan)

    def cert_aps(self) -> tuple[AP, ...]:
        """APs opted into the centralised ACME certificate flow."""

        return tuple(a for a in self.aps if a.cert)

    def ap_by_label(self, label: str) -> AP:
        for a in self.aps:
            if a.label == label:
                return a
        raise ConfigError(f"No [aps.{label}] table in the config")


# ---- validators --------------------------------------------------------


_MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# Multi-label DNS name (at least two labels), lowercase, no trailing dot.
_DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
_VLANS = frozenset({"lan", "iot"})


def _parse_router(d: dict) -> RouterConfig:
    section = "[router]"
    _check_keys(
        d,
        {
            "lan_subnet", "lan_ipv4", "iot_subnet", "iot_ipv4",
            "dns_search_domain", "dhcp", "lan_v6_prefix",
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
        lan_v6_prefix=_req_str(d, "lan_v6_prefix", section),
        lan_subnet=lan_subnet,
        lan_ipv4=lan_ipv4,
        iot_subnet=iot_subnet,
        iot_ipv4=iot_ipv4,
        dns_search_domain=domain,
        dhcp_lan=_parse_dhcp_range(_require_section(dhcp, "lan", f"{section}.dhcp"), f"{section}.dhcp.lan", lan_subnet),
        dhcp_iot=_parse_dhcp_range(_require_section(dhcp, "iot", f"{section}.dhcp"), f"{section}.dhcp.iot", iot_subnet),
    )


def _parse_acme(d: dict) -> AcmeConfig:
    section = "[acme]"
    _check_keys(d, {"domain", "email"}, section)

    domain = _req_str(d, "domain", section)
    if not _DOMAIN_RE.match(domain):
        raise ConfigError(
            f"{section}.domain {domain!r} must be a lowercase multi-label"
            " DNS name (e.g. ap.verson.example.eu)"
        )

    email = _req_str(d, "email", section)
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ConfigError(f"{section}.email {email!r} is not a valid email address")

    return AcmeConfig(domain=domain, email=email)


# Keys under [aps] that belong to the *build* side (parsed strictly by
# aps/apbuild). Listed here only so a typo in an AP table still raises
# instead of being silently ignored by this parser.
_AP_BUILD_KEYS = frozenset({
    "target", "profile", "version", "sha256", "extra_packages", "radios",
})

# Scalar/array keys directly under [aps] — fleet-wide settings rather
# than access points. Everything else under [aps] whose value is a table
# is an AP.
_APS_FLEET_KEYS = frozenset({
    "imagebuilder_base", "timezone", "zonename", "country",
    "syslog_port", "syslog_proto", "trunk_port_alias", "iot_vlan",
    "packages_add", "packages_remove", "ssids",
})


def _parse_aps(
    d: object, router: RouterConfig, acme: AcmeConfig | None
) -> tuple[AP, ...]:
    """Split ``[aps]`` into fleet settings and per-AP tables.

    The discriminator is the *value type*: a table is an access point, a
    scalar or array is a fleet-wide setting. That is why the fleet
    settings are flat and ``ssids`` is an array-of-tables — a nested
    ``[aps.syslog]`` table would be indistinguishable from an AP labelled
    ``syslog``.
    """

    if not d:
        return ()
    if not isinstance(d, dict):
        raise ConfigError("[aps] must be a table")

    aps: list[AP] = []
    for key, body in d.items():
        if isinstance(body, dict):
            aps.append(_parse_ap(key, body, router, acme))
        elif key not in _APS_FLEET_KEYS:
            raise ConfigError(
                f"[aps]: unknown fleet setting {key!r}"
                f" (known: {sorted(_APS_FLEET_KEYS)}). An access point must be"
                " a table, i.e. [aps.<label>]."
            )
    return tuple(aps)


def _parse_ap(
    label: str, d: dict, router: RouterConfig, acme: AcmeConfig | None
) -> AP:
    section = f"[aps.{label}]"
    if not _NAME_RE.match(label):
        raise ConfigError(
            f"{section}: label {label!r} must be lowercase letters/digits/hyphen,"
            " starting with a letter (it becomes a DNS label)"
        )
    _check_keys(
        d,
        {"hostname", "ipv4", "cert", "aliases"} | _AP_BUILD_KEYS,
        section,
    )

    hostname = _req_str(d, "hostname", section)
    if not _NAME_RE.match(hostname):
        raise ConfigError(
            f"{section}.hostname {hostname!r} must be lowercase"
            " letters/digits/hyphen, starting with a letter (DNS-label safe)"
        )

    # APs are always on the trusted bridge and always own their IP
    # statically, so the checks are the lan-subnet ones only.
    ipv4 = _parse_ipv4(_req_str(d, "ipv4", section), f"{section}.ipv4")
    _check_lan_address(ipv4, router, section)

    cert = False
    if "cert" in d:
        v = d["cert"]
        if not isinstance(v, bool):
            raise ConfigError(f"{section}.cert must be bool, got {type(v).__name__}")
        cert = v
    if cert and acme is None:
        raise ConfigError(f"{section}: cert = true requires an [acme] section")

    aliases: tuple[str, ...] = ()
    if "aliases" in d:
        raw = _req_str_list(d, "aliases", section, allow_empty=True)
        for a in raw:
            if not _NAME_RE.match(a):
                raise ConfigError(
                    f"{section}.aliases: {a!r} must be a DNS-label-safe"
                    " lowercase identifier"
                )
        aliases = tuple(raw)

    return AP(
        label=label, hostname=hostname, ipv4=ipv4, cert=cert, aliases=aliases
    )


def _check_lan_address(
    ipv4: ipaddress.IPv4Address, router: RouterConfig, section: str
) -> None:
    """Reject an address outside the LAN subnet or inside its DHCP pool."""

    if ipv4 not in router.lan_subnet:
        raise ConfigError(
            f"{section}.ipv4 ({ipv4}) outside the lan subnet ({router.lan_subnet})"
        )
    host_idx = int(ipv4) - int(router.lan_subnet.network_address)
    lo = router.dhcp_lan.pool_offset
    hi = lo + router.dhcp_lan.pool_size - 1
    if lo <= host_idx <= hi:
        raise ConfigError(
            f"{section}.ipv4 ({ipv4}) falls inside the lan dynamic DHCP pool"
            f" [.{lo}..{hi}] — pick an address outside the pool so the server"
            " can't hand it out to a different MAC"
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


def _check_uniqueness(hosts: tuple[Host, ...], aps: tuple[AP, ...]) -> None:
    """Enforce one namespace across ``[[hosts]]`` *and* ``[aps.*]``.

    Both end up in the same ``/etc/hosts`` and the same DNS zone, so a
    name, alias or address may only be claimed once — collisions across
    the two sections are exactly as broken as collisions within one.
    """

    seen_names: dict[str, str] = {}
    seen_ips: dict[ipaddress.IPv4Address, str] = {}
    seen_macs: dict[str, str] = {}

    # (owner, names it claims, ipv4, mac) for both collections. The owner
    # string is qualified (host/AP) because a host and an AP may well
    # share a bare name — that collision is precisely what we're
    # detecting, so an unqualified owner would make the error unreadable.
    entries: list[tuple[str, tuple[str, ...], ipaddress.IPv4Address, str | None]] = [
        *((f"host {h.name!r}", (h.name, *h.aliases), h.ipv4, h.mac) for h in hosts),
        *(
            (f"AP {a.label!r}", (a.hostname, a.label, *a.aliases), a.ipv4, None)
            for a in aps
        ),
    ]

    for owner, names, ipv4, mac in entries:
        # Dedupe within the entry first: an AP whose label equals its
        # hostname legitimately claims the same string twice. Across
        # entries, any repeat is an error.
        for name in dict.fromkeys(names):
            if name in seen_names:
                raise ConfigError(
                    f"name/alias {name!r} declared twice"
                    f" (first by {seen_names[name]}, again by {owner})"
                )
            seen_names[name] = owner
        if ipv4 in seen_ips:
            raise ConfigError(
                f"IPv4 {ipv4} declared twice"
                f" (first by {seen_ips[ipv4]}, again by {owner})"
            )
        seen_ips[ipv4] = owner
        if mac is not None:
            if mac in seen_macs:
                raise ConfigError(
                    f"MAC {mac} declared twice"
                    f" (first by {seen_macs[mac]}, again by {owner})"
                )
            seen_macs[mac] = owner


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
