"""Overlay stage — copy ``common/files`` into ``build/<ap>/files``.

Image Builder's ``FILES=`` takes a single directory to overlay onto the
root filesystem. With the TOML-driven config model, per-AP overlays
have been eliminated: the per-AP knobs (hostname, management_ip,
radios, PSKs) flow through ``apbuild.render`` generators that write
their output directly into the staged tree after this stage runs.

So ``common/files`` is the only source overlay. It carries the few
files that have no per-AP variation (dropbear config, dhcp stub,
sysctl hardening).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import APSpec, COMMON_DIR


class StageError(Exception):
    """Source directory missing or overlay copy failed."""


def merge(spec: APSpec) -> Path:
    """Build ``aps/build/<ap>/files`` from ``common/files``.

    Returns the staged directory path. Always starts from an empty
    staged tree so removed/renamed files don't linger between builds.
    """

    common_files = COMMON_DIR / "files"
    if not common_files.is_dir():
        raise StageError(f"Common overlay missing: {common_files}")

    if spec.staged_files_dir.exists():
        shutil.rmtree(spec.staged_files_dir)
    spec.staged_files_dir.mkdir(parents=True)

    shutil.copytree(common_files, spec.staged_files_dir, dirs_exist_ok=True)
    return spec.staged_files_dir
