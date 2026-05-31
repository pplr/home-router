"""Build-time generators driven by the repo-root ``hosts.toml``.

Three artifacts are produced from one declarative file:
  - ``/etc/systemd/network/30-br-{lan,iot}.network.d/10-static-leases.conf``
  - ``/etc/systemd/resolved.conf.d/local-zone.conf``
  - ``/etc/hosts``

Stdlib only. f-string composition, no Jinja. Mirrors the discipline of
``aps/apbuild/``.
"""

from .config import HostsConfig, ConfigError, Host, RouterConfig, DhcpRange

__all__ = ["HostsConfig", "ConfigError", "Host", "RouterConfig", "DhcpRange"]
