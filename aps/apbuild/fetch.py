"""Image Builder tarball download, sha256 verification, and extraction.

Re-runs are no-ops: a verified tarball stays in ``aps/downloads/``, an
extracted Image Builder tree stays in ``aps/build/<ap>/imagebuilder/``.
This keeps repeated builds fast and offline-safe once the cache is warm.

OpenWrt 25.12+ ships Image Builder as ``.tar.zst``. We prefer Python's
``compression.zstd`` (stdlib in 3.14+) and fall back to the system
``zstd`` binary on older interpreters so we keep the "no third-party
deps, no venv" guarantee.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from .config import APSpec, CommonConfig, DOWNLOADS_DIR


class FetchError(Exception):
    """Download or extraction failed (network, hash mismatch, bad archive)."""


def ensure(spec: APSpec, common: CommonConfig) -> Path:
    """Download + verify + extract the Image Builder; return its directory.

    The returned path is the top-level Image Builder directory ready for
    ``make image`` (the one containing ``Makefile`` and ``.config``).
    """

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    spec.build_dir.mkdir(parents=True, exist_ok=True)

    _ensure_tarball(spec, common)
    _ensure_extracted(spec)
    return spec.imagebuilder_dir


def tarball_url(spec: APSpec, common: CommonConfig) -> str:
    """Build the canonical Image Builder URL for ``spec``."""

    return (
        f"{common.imagebuilder_base}/{spec.version}/targets/"
        f"{spec.target}/{spec.tarball_name}"
    )


# ---- Download ----------------------------------------------------------


def _ensure_tarball(spec: APSpec, common: CommonConfig) -> None:
    """Download the tarball if missing; verify sha256 in all cases.

    A cached tarball with a wrong hash is treated as fatal — we don't
    silently re-download, since that would mask a corrupted cache or a
    stale pin.
    """

    url = tarball_url(spec, common)

    if spec.tarball_path.exists():
        actual = _sha256(spec.tarball_path)
        if actual == spec.sha256:
            return
        raise FetchError(
            f"Cached tarball {spec.tarball_path} sha256 mismatch:\n"
            f"  expected: {spec.sha256}\n"
            f"  actual:   {actual}\n"
            f"Delete the file and re-run if you intentionally bumped the version."
        )

    print(f"[fetch] downloading {url}")
    tmp = spec.tarball_path.with_suffix(spec.tarball_path.suffix + ".part")
    try:
        with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"Download failed: {url}: {exc}") from exc

    actual = _sha256(tmp)
    if actual != spec.sha256:
        tmp.unlink(missing_ok=True)
        raise FetchError(
            f"Downloaded tarball sha256 mismatch for {url}:\n"
            f"  expected: {spec.sha256}\n"
            f"  actual:   {actual}"
        )
    tmp.replace(spec.tarball_path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- Extract -----------------------------------------------------------


def _ensure_extracted(spec: APSpec) -> None:
    """Extract the tarball to ``spec.imagebuilder_dir`` if not already there.

    Presence of ``Makefile`` inside the expected top-level dir is the
    cache-hit signal — partial extractions (interrupted run) are not
    auto-detected; the user should ``rm -rf aps/build/<ap>/imagebuilder``
    to recover.
    """

    if (spec.imagebuilder_dir / "Makefile").is_file():
        return

    # Clean any stale partial extraction.
    if spec.imagebuilder_dir.exists():
        shutil.rmtree(spec.imagebuilder_dir)
    spec.imagebuilder_dir.parent.mkdir(parents=True, exist_ok=True)

    print(f"[fetch] extracting {spec.tarball_path}")
    _extract_tar_zst(spec.tarball_path, spec.imagebuilder_dir.parent)

    if not (spec.imagebuilder_dir / "Makefile").is_file():
        raise FetchError(
            f"Extraction did not produce expected Image Builder layout at "
            f"{spec.imagebuilder_dir} (missing Makefile). The tarball may have "
            f"changed its top-level directory name; check {spec.tarball_path}."
        )


def _extract_tar_zst(archive: Path, dest: Path) -> None:
    """Extract a ``.tar.zst`` archive into ``dest``.

    Prefers ``compression.zstd`` (stdlib, Python 3.14+) for hermeticity.
    Falls back to piping through the system ``zstd`` binary on older
    interpreters — still no third-party Python deps, just a tool that
    is universally available on Linux build hosts.
    """

    try:
        # Python 3.14+ ships zstd in the stdlib via ``compression.zstd``.
        from compression.zstd import ZstdFile  # type: ignore[import-not-found]
    except ImportError:
        ZstdFile = None

    if ZstdFile is not None:
        with ZstdFile(str(archive), mode="rb") as zfp:
            with tarfile.open(fileobj=zfp, mode="r|") as tar:
                _safe_extract(tar, dest)
        return

    # Fallback: `zstd -dc <archive> | tar -x -C <dest>`.
    if shutil.which("zstd") is None:
        raise FetchError(
            "Neither Python's compression.zstd (3.14+) nor the system zstd "
            "binary is available. Install zstd or upgrade Python."
        )
    dest.mkdir(parents=True, exist_ok=True)
    proc_zstd = subprocess.Popen(
        ["zstd", "-dc", str(archive)],
        stdout=subprocess.PIPE,
    )
    assert proc_zstd.stdout is not None
    try:
        with tarfile.open(fileobj=proc_zstd.stdout, mode="r|") as tar:
            _safe_extract(tar, dest)
    finally:
        proc_zstd.stdout.close()
        rc = proc_zstd.wait()
        if rc != 0:
            raise FetchError(f"zstd -dc failed with exit {rc}")


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """``tar.extractall`` with the 3.12+ ``tar`` filter for path-traversal safety.

    The ``tar`` filter blocks path-escape attacks (absolute entry paths,
    ``..`` components, links whose target escapes ``dest``). The stricter
    ``data`` filter additionally rejects absolute *symlink targets*, which
    OpenWrt's imagebuilder tarball legitimately contains (e.g.
    ``staging_dir/host/bin/ldconfig -> /bin/true`` to no-op ldconfig on
    cross-compile hosts). We sha256-verify the tarball before extraction,
    so the cryptographic integrity guarantee is what protects us — the
    filter is just a backstop against bug-class path traversals.
    """

    dest.mkdir(parents=True, exist_ok=True)
    tar.extractall(path=dest, filter="tar")
