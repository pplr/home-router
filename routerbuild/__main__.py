"""Offline driver: render config.toml outputs into a staging directory.

Used for smoke-testing the renderer without bitbake:

    python3 -m routerbuild --config config.toml --out /tmp/staging

Produces under <out>/:
    etc/systemd/network/30-br-lan.network.d/10-static-leases.conf
    etc/systemd/network/30-br-iot.network.d/10-static-leases.conf
    etc/systemd/resolved.conf.d/local-zone.conf
    etc/hosts
    etc/futro-ap-certs/aps.list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, NetworkConfig
from .render import (
    write_ap_cert_list,
    write_etc_hosts,
    write_network_dropins,
    write_resolved_dropin,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render config.toml artifacts.")
    parser.add_argument(
        "--config", type=Path, required=True, help="Path to config.toml"
    )
    parser.add_argument("--out", type=Path, required=True, help="Staging root")
    args = parser.parse_args(argv)

    try:
        cfg = NetworkConfig.load(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sysconf = args.out / "etc"
    write_network_dropins(cfg, sysconf / "systemd" / "network")
    write_resolved_dropin(cfg, sysconf / "systemd" / "resolved.conf.d")
    write_etc_hosts(cfg, sysconf / "hosts")
    write_ap_cert_list(cfg, sysconf / "futro-ap-certs" / "aps.list")
    print(
        f"Wrote {len(cfg.aps)} APs + {len(cfg.hosts)} hosts under {args.out}/",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
