#!/usr/bin/env python3
"""Generate the per-AP TLS material for LuCI HTTPS.

For every AP whose ``hosts.toml`` entry sets ``ap_cert = true``, this
writes three files under ``aps/secrets/tls/<cert-label>/``:

    key.pem              private key — baked into that AP's image only
    csr.pem              CSR for <label>.<ap_cert_domain>, signed by the
                         router via lego's --csr mode
    bootstrap-cert.pem   self-signed cert with the same SAN, so LuCI can
                         serve HTTPS before the first push lands

Why the key is generated here rather than on the AP: a `sysupgrade -n`
wipes the overlay, so a key generated on-device would be destroyed by
every reflash and force a re-issue. Baking it keeps the AP's identity
stable and the whole fleet reproducible from this repo. The key is
written into exactly one image and is never sent to the router — the
router only ever handles ``csr.pem`` and the signed certificate, both
public. The trade-off is that the key rotates when you rebuild and
reflash, not on every 60-day renewal; rerun with --force to rotate.

Stdlib only, shelling out to `openssl` (same tool the READMEs already
assume). Run from anywhere:

    ./aps/gen-ap-tls.py            # generate anything missing
    ./aps/gen-ap-tls.py --force    # rotate every key (needs a reflash)
    ./aps/gen-ap-tls.py ax59u      # just one AP
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apbuild.config import SECRETS_DIR, ConfigError, FleetConfig

# Self-signed placeholder lifetime. Long enough that it never expires
# before the router's first push in any plausible bring-up, short enough
# that a forgotten bootstrap cert eventually makes noise.
BOOTSTRAP_DAYS = 825

# EC P-256: what LuCI's own px5g defaults to, widely supported, and much
# cheaper than RSA on these SoCs.
KEY_ALGO = ["-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"]


class GenError(Exception):
    """openssl missing or a subcommand failed."""


def main(argv: Sequence[str] | None = None) -> int:
    try:
        fleet = FleetConfig.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    certed = {n: s for n, s in fleet.aps.items() if s.cert_fqdn is not None}

    parser = argparse.ArgumentParser(
        description="Generate per-AP TLS keys, CSRs and bootstrap certificates.",
        epilog=(
            "APs with ap_cert = true in hosts.toml: "
            + (", ".join(sorted(certed)) or "(none)")
        ),
    )
    parser.add_argument(
        "ap",
        nargs="?",
        default="all",
        choices=[*sorted(certed), "all"],
        help="Which AP to generate for (default: all).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if material exists — rotates the key, so the AP "
             "must be rebuilt and reflashed, and the router re-issues its cert.",
    )
    args = parser.parse_args(argv)

    if not certed:
        print(
            "error: no AP has ap_cert = true in hosts.toml — nothing to generate",
            file=sys.stderr,
        )
        return 3

    if shutil.which("openssl") is None:
        print("error: openssl not found in PATH", file=sys.stderr)
        return 2

    targets = sorted(certed) if args.ap == "all" else [args.ap]

    try:
        for name in targets:
            _generate_one(certed[name].cert_label, certed[name].cert_fqdn, args.force)
    except GenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    return 0


def _generate_one(label: str, fqdn: str, force: bool) -> None:
    out_dir = SECRETS_DIR / "tls" / label
    key = out_dir / "key.pem"
    csr = out_dir / "csr.pem"
    cert = out_dir / "bootstrap-cert.pem"

    if key.exists() and not force:
        # Still (re)build the CSR and bootstrap cert if they're missing or
        # the FQDN changed — those are derived, and regenerating them from
        # the existing key is harmless and keeps the key stable.
        if csr.exists() and cert.exists():
            print(f"[tls] {label}: already present ({fqdn}) — skipping")
            return
        print(f"[tls] {label}: key present, regenerating CSR + bootstrap cert")
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"[tls] {label}: generating key ({fqdn})")
        _openssl(["genpkey", *KEY_ALGO, "-out", str(key)])
        os.chmod(key, 0o600)

    out_dir.mkdir(parents=True, exist_ok=True)

    # subjectAltName is what browsers actually check; CN is legacy but
    # harmless and makes `openssl x509 -subject` readable.
    san = f"subjectAltName=DNS:{fqdn}"

    print(f"[tls] {label}: writing CSR")
    _openssl([
        "req", "-new",
        "-key", str(key),
        "-out", str(csr),
        "-subj", f"/CN={fqdn}",
        "-addext", san,
    ])
    os.chmod(csr, 0o644)

    print(f"[tls] {label}: writing self-signed bootstrap certificate")
    _openssl([
        "req", "-x509", "-new",
        "-key", str(key),
        "-out", str(cert),
        "-days", str(BOOTSTRAP_DAYS),
        "-subj", f"/CN={fqdn}",
        "-addext", san,
    ])
    os.chmod(cert, 0o644)


def _openssl(args: list[str]) -> None:
    result = subprocess.run(
        ["openssl", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GenError(
            f"openssl {' '.join(args)} failed with exit {result.returncode}:\n"
            f"{result.stderr.strip()}"
        )


if __name__ == "__main__":
    sys.exit(main())
