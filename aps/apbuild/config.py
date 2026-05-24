"""Central fleet config — parsed from ``aps/config.toml`` via stdlib tomllib.

A single declarative file is the source of truth for every per-AP knob
(target, profile, version, hostname, sha256, management_ip, radios,
extra_packages) plus every shared one (timezone, syslog endpoint, SSIDs,
uplink port name, package set, imagebuilder URL).

The parser is strict: unknown keys raise ``ConfigError`` with the offending
section path. Typos in config fail fast rather than silently no-op.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Repo-root-relative paths. Resolved once at module load so callers don't
# depend on the working directory.
APS_ROOT: Path = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR: Path = APS_ROOT / "downloads"
BUILD_DIR: Path = APS_ROOT / "build"
COMMON_DIR: Path = APS_ROOT / "common"
SECRETS_DIR: Path = APS_ROOT / "secrets"
CONFIG_PATH: Path = APS_ROOT / "config.toml"


class ConfigError(Exception):
    """Malformed or missing fleet config."""


# ---- dataclasses (all frozen — mutation = bug) -------------------------


@dataclass(frozen=True)
class RouterAddrs:
    ipv4: str
    ipv6: str
    lan_v6_prefix: str


@dataclass(frozen=True)
class Syslog:
    ip: str
    port: int
    proto: str


@dataclass(frozen=True)
class Uplink:
    trunk_port_alias: str
    iot_vlan: int


@dataclass(frozen=True)
class Ssid:
    """One VAP definition broadcast on every non-disabled radio.

    ``key`` is the TOML table key (e.g. "trusted", "iot") used as part
    of the generated UCI section name; ``name`` is the visible SSID.
    """

    key: str
    name: str
    network: str
    encryption: str
    ieee80211w: str
    psk_secret: str


@dataclass(frozen=True)
class Packages:
    add: tuple[str, ...]
    remove: tuple[str, ...]


@dataclass(frozen=True)
class Radio:
    """One wifi-device entry. ``index`` becomes ``radio<index>`` in UCI.

    Disabled radios carry only ``path``; their band/htmode/channel are
    optional (the wifi-device entry gets ``option disabled '1'`` and no
    VAPs are attached).
    """

    index: int
    path: str
    disabled: bool = False
    band: str | None = None
    htmode: str | None = None
    channel: int | None = None


@dataclass(frozen=True)
class CommonConfig:
    imagebuilder_base: str
    timezone: str
    zonename: str
    country: str
    router: RouterAddrs
    syslog: Syslog
    uplink: Uplink
    packages: Packages
    ssids: tuple[Ssid, ...]


@dataclass(frozen=True)
class APSpec:
    name: str
    hostname: str
    target: str
    version: str
    profile: str
    sha256: str
    management_ip: str
    extra_packages: tuple[str, ...]
    radios: tuple[Radio, ...]

    # ---- Derived URL / filesystem paths --------------------------------

    @property
    def tarball_name(self) -> str:
        # OpenWrt 25.12 uses ``-`` to join the two target halves (e.g.
        # ``qualcommax-ipq807x``) in the tarball name itself.
        return (
            f"openwrt-imagebuilder-{self.version}-"
            f"{self.target.replace('/', '-')}.Linux-x86_64.tar.zst"
        )

    @property
    def tarball_path(self) -> Path:
        return DOWNLOADS_DIR / self.tarball_name

    @property
    def build_dir(self) -> Path:
        return BUILD_DIR / self.name

    @property
    def imagebuilder_dir(self) -> Path:
        return self.build_dir / "imagebuilder" / self.tarball_name.removesuffix(".tar.zst")

    @property
    def staged_files_dir(self) -> Path:
        return self.build_dir / "files"

    @property
    def out_dir(self) -> Path:
        return self.build_dir / "out"


@dataclass(frozen=True)
class FleetConfig:
    common: CommonConfig
    aps: dict[str, APSpec]

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> FleetConfig:
        if not path.is_file():
            raise ConfigError(f"Fleet config not found: {path}")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: TOML parse error: {exc}") from exc

        _check_keys(data, {"common", "aps"}, f"{path} (top level)")

        common = _parse_common(_require_section(data, "common", path))
        aps_raw = _require_section(data, "aps", path)
        aps = {name: _parse_ap(name, body) for name, body in aps_raw.items()}
        if not aps:
            raise ConfigError(f"{path}: [aps] must contain at least one AP")

        return cls(common=common, aps=aps)

    def packages_for(self, name: str) -> tuple[str, ...]:
        """Build the Image Builder ``PACKAGES=`` token list for AP ``name``.

        Order: common.add, then ``-`` for each common.remove, then
        per-AP extra_packages. Deduplication preserves first occurrence.
        """

        spec = self.aps[name]
        seen: set[str] = set()
        ordered: list[str] = []
        for pkg in (
            *self.common.packages.add,
            *(f"-{p}" for p in self.common.packages.remove),
            *spec.extra_packages,
        ):
            if pkg not in seen:
                seen.add(pkg)
                ordered.append(pkg)
        return tuple(ordered)


# ---- Validators --------------------------------------------------------


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_TARGET_RE = re.compile(r"^[a-z0-9]+/[a-z0-9]+$")

# Bands and channel widths accepted by OpenWrt 24.10+ / mac80211.
_VALID_BANDS = frozenset({"2g", "5g", "6g"})
_VALID_HTMODES = frozenset({
    "HT20", "HT40",
    "VHT20", "VHT40", "VHT80", "VHT160",
    "HE20", "HE40", "HE80", "HE160",
    "EHT20", "EHT40", "EHT80", "EHT160", "EHT320",
})
_VALID_ENCRYPTIONS = frozenset({
    "none", "wep",
    "psk", "psk2", "psk-mixed",
    "sae", "sae-mixed", "owe",
})
_VALID_PMF = frozenset({"0", "1", "2"})


# ---- Section parsers ---------------------------------------------------


def _parse_common(d: dict) -> CommonConfig:
    section = "[common]"
    _check_keys(d, {
        "imagebuilder_base", "timezone", "zonename", "country",
        "router", "syslog", "uplink", "packages", "ssids",
    }, section)

    return CommonConfig(
        imagebuilder_base=_req_str(d, "imagebuilder_base", section),
        timezone=_req_str(d, "timezone", section),
        zonename=_req_str(d, "zonename", section),
        country=_req_str(d, "country", section),
        router=_parse_router(_require_section(d, "router", section)),
        syslog=_parse_syslog(_require_section(d, "syslog", section)),
        uplink=_parse_uplink(_require_section(d, "uplink", section)),
        packages=_parse_packages(_require_section(d, "packages", section)),
        ssids=_parse_ssids(_require_section(d, "ssids", section)),
    )


def _parse_router(d: dict) -> RouterAddrs:
    section = "[common.router]"
    _check_keys(d, {"ipv4", "ipv6", "lan_v6_prefix"}, section)
    ipv4 = _req_str(d, "ipv4", section)
    _validate_ipv4(ipv4, f"{section}.ipv4")
    return RouterAddrs(
        ipv4=ipv4,
        ipv6=_req_str(d, "ipv6", section),
        lan_v6_prefix=_req_str(d, "lan_v6_prefix", section),
    )


def _parse_syslog(d: dict) -> Syslog:
    section = "[common.syslog]"
    _check_keys(d, {"ip", "port", "proto"}, section)
    ip = _req_str(d, "ip", section)
    _validate_ipv4(ip, f"{section}.ip")
    port = _req_int(d, "port", section)
    if not (1 <= port <= 65535):
        raise ConfigError(f"{section}.port out of range: {port}")
    proto = _req_str(d, "proto", section)
    if proto not in {"udp", "tcp"}:
        raise ConfigError(f"{section}.proto must be 'udp' or 'tcp', got {proto!r}")
    return Syslog(ip=ip, port=port, proto=proto)


def _parse_uplink(d: dict) -> Uplink:
    section = "[common.uplink]"
    _check_keys(d, {"trunk_port_alias", "iot_vlan"}, section)
    vlan = _req_int(d, "iot_vlan", section)
    if not (1 <= vlan <= 4094):
        raise ConfigError(f"{section}.iot_vlan out of range: {vlan}")
    return Uplink(
        trunk_port_alias=_req_str(d, "trunk_port_alias", section),
        iot_vlan=vlan,
    )


def _parse_packages(d: dict) -> Packages:
    section = "[common.packages]"
    _check_keys(d, {"add", "remove"}, section)
    return Packages(
        add=tuple(_req_str_list(d, "add", section, allow_empty=True)),
        remove=tuple(_req_str_list(d, "remove", section, allow_empty=True)),
    )


def _parse_ssids(d: dict) -> tuple[Ssid, ...]:
    if not d:
        raise ConfigError("[common.ssids] must contain at least one SSID definition")
    out: list[Ssid] = []
    for key, body in d.items():
        if not isinstance(body, dict):
            raise ConfigError(f"[common.ssids.{key}] must be a table")
        section = f"[common.ssids.{key}]"
        _check_keys(body, {"name", "network", "encryption", "ieee80211w", "psk_secret"}, section)
        enc = _req_str(body, "encryption", section)
        if enc not in _VALID_ENCRYPTIONS:
            raise ConfigError(
                f"{section}.encryption invalid: {enc!r} "
                f"(allowed: {sorted(_VALID_ENCRYPTIONS)})"
            )
        pmf = _req_str(body, "ieee80211w", section)
        if pmf not in _VALID_PMF:
            raise ConfigError(f"{section}.ieee80211w must be '0' | '1' | '2', got {pmf!r}")
        out.append(Ssid(
            key=key,
            name=_req_str(body, "name", section),
            network=_req_str(body, "network", section),
            encryption=enc,
            ieee80211w=pmf,
            psk_secret=_req_str(body, "psk_secret", section),
        ))
    return tuple(out)


def _parse_ap(name: str, d: dict) -> APSpec:
    section = f"[aps.{name}]"
    if not isinstance(d, dict):
        raise ConfigError(f"{section} must be a table")
    _check_keys(d, {
        "hostname", "target", "version", "profile", "sha256",
        "management_ip", "extra_packages", "radios",
    }, section)

    target = _req_str(d, "target", section)
    if not _TARGET_RE.match(target):
        raise ConfigError(
            f"{section}.target: expected '<a>/<b>' (e.g. qualcommax/ipq807x), got {target!r}"
        )
    version = _req_str(d, "version", section)
    if not _VERSION_RE.match(version):
        raise ConfigError(f"{section}.version: expected MAJOR.MINOR.PATCH, got {version!r}")
    sha = _req_str(d, "sha256", section)
    if not _SHA256_RE.match(sha):
        raise ConfigError(f"{section}.sha256: expected 64-hex-char sha256, got {sha!r}")
    mip = _req_str(d, "management_ip", section)
    _validate_ipv4(mip, f"{section}.management_ip")

    radios_raw = d.get("radios")
    if not isinstance(radios_raw, list) or not radios_raw:
        raise ConfigError(f"{section}.radios must be a non-empty array")
    radios = tuple(_parse_radio(i, r, name) for i, r in enumerate(radios_raw))

    extra_pkgs = tuple(
        _req_str_list(d, "extra_packages", section, allow_empty=True)
        if "extra_packages" in d
        else []
    )

    return APSpec(
        name=name,
        hostname=_req_str(d, "hostname", section),
        target=target,
        version=version,
        profile=_req_str(d, "profile", section),
        sha256=sha,
        management_ip=mip,
        extra_packages=extra_pkgs,
        radios=radios,
    )


def _parse_radio(index: int, d: object, ap_name: str) -> Radio:
    section = f"[[aps.{ap_name}.radios]] (index {index})"
    if not isinstance(d, dict):
        raise ConfigError(f"{section} must be a table")
    _check_keys(d, {"path", "disabled", "band", "htmode", "channel"}, section)

    path = _req_str(d, "path", section)
    disabled = bool(d.get("disabled", False))

    if disabled:
        # Disabled radios need only ``path``; band/htmode/channel are
        # accepted but unused, so toggling ``disabled`` later doesn't
        # force re-typing them.
        return Radio(index=index, path=path, disabled=True)

    band = _req_str(d, "band", section)
    if band not in _VALID_BANDS:
        raise ConfigError(
            f"{section}.band invalid: {band!r} (allowed: {sorted(_VALID_BANDS)})"
        )
    htmode = _req_str(d, "htmode", section)
    if htmode not in _VALID_HTMODES:
        raise ConfigError(
            f"{section}.htmode invalid: {htmode!r} (allowed: {sorted(_VALID_HTMODES)})"
        )
    if "channel" not in d:
        raise ConfigError(f"{section} missing required key 'channel'")
    channel = d["channel"]
    if not isinstance(channel, int) or isinstance(channel, bool):
        raise ConfigError(f"{section}.channel must be int, got {type(channel).__name__}")

    return Radio(index=index, path=path, disabled=False, band=band, htmode=htmode, channel=channel)


# ---- Helpers -----------------------------------------------------------


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
    # bool is a subclass of int in Python — reject explicitly.
    if not isinstance(v, int) or isinstance(v, bool):
        raise ConfigError(f"{section}.{key} must be int, got {type(v).__name__}")
    return v


def _req_str_list(d: dict, key: str, section: str, *, allow_empty: bool = False) -> list[str]:
    if key not in d:
        raise ConfigError(f"{section} missing required key {key!r}")
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


def _validate_ipv4(value: str, source: str) -> None:
    try:
        ipaddress.IPv4Address(value)
    except (ValueError, ipaddress.AddressValueError) as exc:
        raise ConfigError(f"{source} not a valid IPv4 address: {value!r}") from exc
