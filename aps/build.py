#!/usr/bin/env python3
"""Build a sysupgrade image for one (or all) APs declared in config.toml.

Usage:
    ./aps/build.py ax3600          # build just the AX3600 image
    ./aps/build.py r3g             # build just the R3G image
    ./aps/build.py all             # build every AP in config.toml
    ./aps/build.py --dump ax3600   # print resolved spec as JSON, no build

Stdlib only — no venv, no pip. The actual work lives in ``apbuild/``;
this file is just the argparse front-end so the build can be driven
straight from a shell with no path magic.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Sequence

# Allow running the script directly without installing the package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apbuild.config import ConfigError, FleetConfig
from apbuild.fetch import tarball_url
from apbuild.invoke import BuildError, build_one
from apbuild.secrets import MissingSecretError


def main(argv: Sequence[str] | None = None) -> int:
    try:
        fleet = FleetConfig.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    ap_names = sorted(fleet.aps)
    parser = argparse.ArgumentParser(
        description="Build OpenWrt sysupgrade images for the home APs.",
        epilog=f"Discovered APs (from aps/config.toml): {', '.join(ap_names)}",
    )
    parser.add_argument(
        "ap",
        choices=[*ap_names, "all"],
        help="Which AP to build (or 'all' for every declared AP).",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Print resolved spec + common config + packages as JSON; don't build.",
    )
    args = parser.parse_args(argv)

    targets = ap_names if args.ap == "all" else [args.ap]

    if args.dump:
        for name in targets:
            print(json.dumps(_resolved(fleet, name), indent=2))
        return 0

    for name in targets:
        try:
            build_one(name, fleet)
        except MissingSecretError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        except BuildError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 4
        except Exception as exc:  # noqa: BLE001 — top-level catch is intentional
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    return 0


def _resolved(fleet: FleetConfig, name: str) -> dict:
    """Serialize the per-AP build inputs as a dict for `--dump`."""

    spec = fleet.aps[name]
    return {
        "ap": name,
        "spec": dataclasses.asdict(spec),
        "common": dataclasses.asdict(fleet.common),
        "packages": list(fleet.packages_for(name)),
        "tarball_url": tarball_url(spec, fleet.common),
    }


if __name__ == "__main__":
    sys.exit(main())
