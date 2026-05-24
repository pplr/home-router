"""apbuild — stdlib-only OpenWrt Image Builder driver for the home APs.

See ``aps/build.py`` for the CLI entry point and the project's CLAUDE.md
for the surrounding "static-config-from-image" model. Each module here
has a single, narrow responsibility:

- ``config``   — parse ``aps/<ap>/{target,version,profile,...}`` into ``APSpec``
- ``secrets``  — enumerate and read out-of-tree secrets, fail fast on missing
- ``fetch``    — download + sha256-verify + extract the Image Builder tarball
- ``stage``    — overlay-merge ``common/files`` and ``<ap>/files`` into ``build/<ap>/files``
- ``render``   — substitute ``${PSK}``/``${HOSTNAME}`` placeholders and rewrite ``/etc/shadow``
- ``invoke``   — drive ``make image`` and orchestrate the above steps

Python 3.10+, stdlib only (uses ``compression.zstd`` from 3.14+; falls back
to the ``zstd`` system binary on older interpreters).
"""

__all__ = ["config", "fetch", "stage", "render", "invoke", "secrets"]
