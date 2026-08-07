"""Out-of-tree secret loading + fail-fast enumeration.

Mirrors the router's ``read_secret`` discipline (see CLAUDE.md "Out-of-tree
secrets"): each secret lives under ``aps/secrets/`` (gitignored), and any
missing or empty file raises ``MissingSecretError`` with the exact path
the user must populate.

PSK paths are *data-driven*: the build driver reads ``ssid.psk_secret``
from the loaded ``CommonConfig`` and loads one PSK per SSID. The set of
static secrets (root hash, ssh authorized_keys, dropbear host keys) is
still hard-coded since those are universal across the fleet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import APSpec, CommonConfig, SECRETS_DIR


class MissingSecretError(Exception):
    """A required secret file is missing or empty.

    Always carries the absolute path so the user can copy-paste it into
    the generation command from ``aps/secrets/README.md``.
    """


@dataclass(frozen=True)
class Secrets:
    """All secret values needed to bake any AP image.

    ``psks`` is keyed by the ``psk_secret`` path from the TOML so the
    wireless generator can look up the right PSK for each SSID without
    knowing how many SSIDs the fleet defines.
    """

    root_password_hash: str
    authorized_keys: str
    dropbear_ed25519_host_key: bytes
    dropbear_rsa_host_key: bytes
    # Public half of the router's certificate-push key. Installed into
    # every AP's authorized_keys behind a forced command, so the router
    # can deliver a renewed certificate but cannot obtain a shell.
    ap_push_pubkey: str
    psks: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class APTlsSecrets:
    """Per-AP TLS material for LuCI HTTPS.

    The private key is generated once on the build host (see
    ``aps/gen-ap-tls.py``) and baked into this AP's image only — it is
    never transmitted, and the router only ever sees the CSR. Because a
    reflash wipes the overlay, the ``bootstrap_cert`` (self-signed,
    generated alongside the key) keeps LuCI serving HTTPS until the
    router's next push replaces it with the Let's Encrypt one.
    """

    key: str
    bootstrap_cert: str


# Relative paths under SECRETS_DIR for secrets that are constant across
# the fleet (not derived from config.toml).
STATIC_SECRET_FILES: dict[str, str] = {
    "root_password_hash": "root.hash",
    "authorized_keys": "ssh/authorized_keys",
    "dropbear_ed25519_host_key": "ssh/dropbear_ed25519_host_key",
    "dropbear_rsa_host_key": "ssh/dropbear_rsa_host_key",
    "ap_push_pubkey": "ssh/ap-push-key.pub",
}


def load_all(common: CommonConfig) -> Secrets:
    """Read every required secret eagerly; fail fast on any missing one.

    PSK file paths are pulled from ``common.ssids[*].psk_secret`` so
    adding/removing an SSID in ``config.toml`` automatically changes
    which PSK files are required (or no longer required).
    """

    psks: dict[str, str] = {}
    for ssid in common.ssids:
        psks[ssid.psk_secret] = _read_text(ssid.psk_secret)

    return Secrets(
        root_password_hash=_read_text(STATIC_SECRET_FILES["root_password_hash"]),
        authorized_keys=_read_text(
            STATIC_SECRET_FILES["authorized_keys"], strip=False
        ),
        dropbear_ed25519_host_key=_read_bytes(
            STATIC_SECRET_FILES["dropbear_ed25519_host_key"]
        ),
        dropbear_rsa_host_key=_read_bytes(
            STATIC_SECRET_FILES["dropbear_rsa_host_key"]
        ),
        ap_push_pubkey=_read_text(STATIC_SECRET_FILES["ap_push_pubkey"]),
        psks=psks,
    )


def load_ap_tls(spec: APSpec) -> APTlsSecrets | None:
    """Read this AP's baked TLS key + bootstrap certificate.

    Returns ``None`` when the AP is not opted into the certificate flow
    (no ``ap_cert = true`` on its hosts.toml entry), in which case the
    image is built without TLS material and LuCI stays on plain HTTP.

    Keyed by ``cert_label`` (the certificate's leftmost DNS label, e.g.
    ``ax59u``) rather than the AP's config key so the on-disk layout
    mirrors the issued names.
    """

    if spec.cert_label is None:
        return None

    base = f"tls/{spec.cert_label}"
    return APTlsSecrets(
        key=_read_text(f"{base}/key.pem", strip=False),
        bootstrap_cert=_read_text(f"{base}/bootstrap-cert.pem", strip=False),
    )


def _read_text(rel: str, *, strip: bool = True) -> str:
    path = SECRETS_DIR / rel
    _ensure_present(path)
    content = path.read_text(encoding="utf-8")
    return content.strip() if strip else content


def _read_bytes(rel: str) -> bytes:
    path = SECRETS_DIR / rel
    _ensure_present(path)
    return path.read_bytes()


def _ensure_present(path: Path) -> None:
    if not path.is_file():
        raise MissingSecretError(
            f"Missing secret: {path} (see aps/secrets/README.md for the generation command)"
        )
    if path.stat().st_size == 0:
        raise MissingSecretError(f"Secret is empty: {path}")
