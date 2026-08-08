"""Fleet build config — parsed from the repo-root ``config.toml``.

That one file is the source of truth for the whole network. This module
owns the *build* slice of it: every per-AP knob (target, profile,
version, sha256, radios, extra_packages) plus the fleet-wide ones under
``[aps]`` (timezone, SSIDs, uplink port, package set, imagebuilder URL).

The AP's **identity** (label, hostname, IPv4, cert opt-in) and the
``[router]`` addresses come from :mod:`routerbuild.config`, which parses
the same file — see its docstring for why the split runs that way.
Addresses the APs need (gateway, DNS, NTP, syslog target) are *derived*
from ``[router].lan_ipv4`` rather than repeated, so they cannot drift.

The parser is strict: unknown keys raise ``ConfigError`` with the offending
section path. Typos in config fail fast rather than silently no-op.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Repo-root-relative paths. Resolved once at module load so callers don't
# depend on the working directory.
APS_ROOT: Path = Path(__file__).resolve().parent.parent
REPO_ROOT: Path = APS_ROOT.parent
DOWNLOADS_DIR: Path = APS_ROOT / "downloads"
BUILD_DIR: Path = APS_ROOT / "build"
COMMON_DIR: Path = APS_ROOT / "common"
SECRETS_DIR: Path = APS_ROOT / "secrets"
CONFIG_PATH: Path = REPO_ROOT / "config.toml"

# routerbuild lives at the repo root alongside config.toml.
sys.path.insert(0, str(REPO_ROOT))
from routerbuild.config import NetworkConfig  # noqa: E402


class ConfigError(Exception):
    """Malformed or missing fleet config."""


# ---- dataclasses (all frozen — mutation = bug) -------------------------


@dataclass(frozen=True)
class RouterAddrs:
    """Router addresses as the APs see them.

    Derived from ``[router]`` rather than declared under ``[aps]``: the
    AP's gateway, DNS and NTP server *are* the router's LAN address, and
    repeating it invites drift.
    """

    ipv4: str
    lan_v6_prefix: str


@dataclass(frozen=True)
class Syslog:
    """Where logd ships logs — the router's LAN address, port from ``[aps]``."""

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
    # ``name`` is the [aps.<label>] table key — this AP's single
    # identifier: build target, short DNS alias, and secrets directory.
    name: str
    hostname: str
    target: str
    version: str
    profile: str
    sha256: str
    management_ip: str
    extra_packages: tuple[str, ...]
    radios: tuple[Radio, ...]
    # FQDN of the Let's Encrypt certificate this AP serves LuCI on, or
    # None when its [aps.<label>] table doesn't set `cert = true`. The
    # matching private key is baked from aps/secrets/tls/<cert_label>/
    # and never leaves the device; the router signs the CSR and pushes
    # the certificate back (see the futro-ap-certs recipe).
    cert_fqdn: str | None = None
    cert_label: str | None = None

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
            raise ConfigError(f"Config not found: {path}")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: TOML parse error: {exc}") from exc

        # The same file also carries [router], [acme] and [[hosts]].
        # NetworkConfig validates all of that (addresses, uniqueness) and
        # gives us each AP's identity; errors bubble up as ConfigError.
        net = NetworkConfig.load(path)

        aps_raw = data.get("aps", {})
        if not isinstance(aps_raw, dict):
            raise ConfigError(f"{path}: [aps] must be a table")

        common = _parse_common(aps_raw, net)
        aps = {
            ap.label: _parse_ap(ap.label, aps_raw[ap.label], ap, net)
            for ap in net.aps
        }
        if not aps:
            raise ConfigError(f"{path}: no [aps.<label>] table declared")

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


def _parse_common(d: dict, net: NetworkConfig) -> CommonConfig:
    """Read the fleet-wide settings sitting directly under ``[aps]``.

    Per-AP tables are skipped here (``routerbuild`` already split them
    out); only the scalar/array keys are fleet settings. Router-derived
    values are taken from ``net`` rather than re-declared.
    """

    section = "[aps]"
    port = _req_int(d, "syslog_port", section)
    if not (1 <= port <= 65535):
        raise ConfigError(f"{section}.syslog_port out of range: {port}")
    proto = _req_str(d, "syslog_proto", section)
    if proto not in {"udp", "tcp"}:
        raise ConfigError(
            f"{section}.syslog_proto must be 'udp' or 'tcp', got {proto!r}"
        )
    vlan = _req_int(d, "iot_vlan", section)
    if not (1 <= vlan <= 4094):
        raise ConfigError(f"{section}.iot_vlan out of range: {vlan}")

    router_ipv4 = str(net.router.lan_ipv4)
    return CommonConfig(
        imagebuilder_base=_req_str(d, "imagebuilder_base", section),
        timezone=_req_str(d, "timezone", section),
        zonename=_req_str(d, "zonename", section),
        country=_req_str(d, "country", section),
        # Derived from [router]: the AP's gateway/DNS/NTP server and its
        # syslog target are all the router's LAN address.
        router=RouterAddrs(
            ipv4=router_ipv4,
            lan_v6_prefix=net.router.lan_v6_prefix,
        ),
        syslog=Syslog(ip=router_ipv4, port=port, proto=proto),
        uplink=Uplink(
            trunk_port_alias=_req_str(d, "trunk_port_alias", section),
            iot_vlan=vlan,
        ),
        packages=Packages(
            add=tuple(_req_str_list(d, "packages_add", section, allow_empty=True)),
            remove=tuple(
                _req_str_list(d, "packages_remove", section, allow_empty=True)
            ),
        ),
        ssids=_parse_ssids(d.get("ssids", [])),
    )


def _parse_ssids(raw: object) -> tuple[Ssid, ...]:
    # Array-of-tables rather than named sub-tables: a nested
    # [aps.ssids.<x>] table would be indistinguishable from an AP.
    if not raw:
        raise ConfigError("[[aps.ssids]] must contain at least one SSID definition")
    if not isinstance(raw, list):
        raise ConfigError("[[aps.ssids]] must be an array of tables")
    out: list[Ssid] = []
    for i, body in enumerate(raw):
        if not isinstance(body, dict):
            raise ConfigError(f"[[aps.ssids]] (index {i}) must be a table")
        key = _req_str(body, "key", f"[[aps.ssids]] (index {i})")
        section = f"[[aps.ssids]] (key {key!r})"
        _check_keys(
            body,
            {"key", "name", "network", "encryption", "ieee80211w", "psk_secret"},
            section,
        )
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


def _parse_ap(name: str, d: dict, ap: "object", net: NetworkConfig) -> APSpec:
    """Parse the *build* half of an ``[aps.<label>]`` table.

    ``ap`` is the already-validated identity (label, hostname, ipv4,
    cert) from :mod:`routerbuild.config`, so the identity keys are
    accepted here without re-checking them.
    """

    section = f"[aps.{name}]"
    if not isinstance(d, dict):
        raise ConfigError(f"{section} must be a table")
    _check_keys(d, {
        "hostname", "ipv4", "cert", "aliases",
        "target", "version", "profile", "sha256",
        "extra_packages", "radios",
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

    radios_raw = d.get("radios")
    if not isinstance(radios_raw, list) or not radios_raw:
        raise ConfigError(f"{section}.radios must be a non-empty array")
    radios = tuple(_parse_radio(i, r, name) for i, r in enumerate(radios_raw))

    extra_pkgs = tuple(
        _req_str_list(d, "extra_packages", section, allow_empty=True)
        if "extra_packages" in d
        else []
    )

    # Certificate identity is opt-in via `cert = true`; when unset the AP
    # is built without TLS material and LuCI stays on plain HTTP.
    cert_fqdn = ap.cert_fqdn(net.acme) if ap.cert else None
    cert_label = ap.label if ap.cert else None

    return APSpec(
        name=name,
        hostname=ap.hostname,
        target=target,
        version=version,
        profile=_req_str(d, "profile", section),
        sha256=sha,
        management_ip=str(ap.ipv4),
        extra_packages=extra_pkgs,
        radios=radios,
        cert_fqdn=cert_fqdn,
        cert_label=cert_label,
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
