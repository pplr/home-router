"""Build-time generators driven by the repo-root ``config.toml``.

Four artifacts are produced from one declarative file:
  - ``/etc/systemd/network/30-br-{lan,iot}.network.d/10-static-leases.conf``
  - ``/etc/systemd/resolved.conf.d/local-zone.conf``
  - ``/etc/hosts``
  - ``/etc/futro-ap-certs/aps.list``

``config.toml`` also carries the OpenWrt AP build spec, which
``aps/apbuild/`` parses from the same file; this package handles the
slice the router needs.

Stdlib only. f-string composition, no Jinja. Mirrors the discipline of
``aps/apbuild/``.
"""

from .config import (
    AP,
    AcmeConfig,
    ConfigError,
    DhcpRange,
    Host,
    NetworkConfig,
    RouterConfig,
)

__all__ = [
    "AP",
    "AcmeConfig",
    "ConfigError",
    "DhcpRange",
    "Host",
    "NetworkConfig",
    "RouterConfig",
]
