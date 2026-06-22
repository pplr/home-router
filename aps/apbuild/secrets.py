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

from .config import CommonConfig, SECRETS_DIR


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
    psks: dict[str, str] = field(default_factory=dict)


# Relative paths under SECRETS_DIR for secrets that are constant across
# the fleet (not derived from config.toml).
STATIC_SECRET_FILES: dict[str, str] = {
    "root_password_hash": "root.hash",
    "authorized_keys": "ssh/authorized_keys",
    "dropbear_ed25519_host_key": "ssh/dropbear_ed25519_host_key",
    "dropbear_rsa_host_key": "ssh/dropbear_rsa_host_key",
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
        psks=psks,
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
