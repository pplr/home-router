"""Orchestrator: load fleet → secrets → fetch → stage → render → ``make image``.

The single public entry point is :func:`build_one`. ``build.py`` (CLI)
calls it once per AP name with the already-loaded :class:`FleetConfig`.
Each step uses fail-fast errors from its own module so the user sees
the first thing that went wrong, not a cascade.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import fetch, render, stage
from .config import APSpec, FleetConfig
from .secrets import load_all


class BuildError(Exception):
    """Image Builder ``make image`` returned a non-zero exit."""


def build_one(name: str, fleet: FleetConfig) -> Path:
    """Build the sysupgrade image for AP ``name``; return the output directory.

    Steps, in order:
      1. Look up the per-AP spec inside the loaded fleet.
      2. Read every secret eagerly (fail before any expensive work).
      3. Download + verify + extract the Image Builder tarball.
      4. Compose the FILES/ overlay from ``common/files``.
      5. Generate and write /etc/config/{system,network,wireless} +
         install secrets into the staged tree.
      6. Run ``make image`` inside the Image Builder.
    """

    if name not in fleet.aps:
        raise BuildError(f"unknown AP {name!r} (known: {sorted(fleet.aps)})")
    spec = fleet.aps[name]

    print(f"[build] {name}: checking secrets")
    secrets = load_all(fleet.common, need_netdata_key=spec.netdata)

    print(f"[build] {name}: ensuring Image Builder {spec.version} for {spec.target}")
    imagebuilder_dir = fetch.ensure(spec, fleet.common)

    print(f"[build] {name}: staging overlay")
    stage.merge(spec)

    print(f"[build] {name}: rendering generated config + secrets")
    render.render_all(spec, fleet.common, secrets)

    print(f"[build] {name}: invoking Image Builder")
    out_dir = _run_make_image(spec, imagebuilder_dir, fleet)

    print(f"[build] {name}: done — images in {out_dir}")
    return out_dir


def _run_make_image(spec: APSpec, imagebuilder_dir: Path, fleet: FleetConfig) -> Path:
    """Run ``make image PROFILE=… PACKAGES=… FILES=… BIN_DIR=…``.

    BIN_DIR collects the sysupgrade.bin under ``aps/build/<ap>/out/`` so
    multiple APs can build in parallel without trampling each other.
    """

    spec.out_dir.mkdir(parents=True, exist_ok=True)
    packages = fleet.packages_for(spec.name)
    cmd = [
        "make",
        "image",
        f"PROFILE={spec.profile}",
        f"PACKAGES={' '.join(packages)}",
        f"FILES={spec.staged_files_dir}",
        f"BIN_DIR={spec.out_dir}",
    ]
    print(f"[build] $ {' '.join(cmd)}  (cwd={imagebuilder_dir})")
    result = subprocess.run(cmd, cwd=imagebuilder_dir, check=False)
    if result.returncode != 0:
        raise BuildError(
            f"`make image` for {spec.name} failed with exit {result.returncode}"
        )
    return spec.out_dir
