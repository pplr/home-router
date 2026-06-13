# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Minimal Yocto Wrynose (6.0) image for a Fujitsu Futro S920 home router.

**Target hardware:** Fujitsu Futro S920 — AMD GX-222GC (x86-64), 4GB RAM, 8GB mSATA SSD, 3 NICs.

## Build Commands

```bash
# Initialize build environment (must be run in each new shell)
source ./layers/openembedded-core/oe-init-build-env build

# Build the image
bitbake core-image-minimal
```

Note: `oe-init-build-env` changes the working directory to `build/`. Requires ~50-100 GB disk space.

## Architecture

- **layers/** — Yocto/OE layers:
  - `openembedded-core` — core metadata and `oe-init-build-env` script (git submodule, `wrynose` branch)
  - `meta-yocto` — Poky distro policy (`meta-poky`) and BSP (`meta-yocto-bsp`) (git submodule, `wrynose` branch)
  - `bitbake` — build engine (git submodule, `wrynose` branch)
  - `meta-rauc` — RAUC update framework layer (git submodule, `master` branch)
  - `meta-futro-s920` — custom BSP layer for the Fujitsu Futro S920 (checked in, not a submodule)
- **build/conf/** — build configuration (checked in):
  - `local.conf` — machine (`futro-s920` default), distro (`poky`), paths, sstate mirrors
  - `bblayers.conf` — active layers: `meta`, `meta-poky`, `meta-yocto-bsp`, `meta-rauc`, `meta-futro-s920`
- **downloads/**, **sstate-cache/** — gitignored build caches

## Key Configuration

- `MACHINE` is set to `futro-s920` — custom machine config using `corei7-64` tune (SSE4.2, no AVX2) with EFI boot.
- `INIT_MANAGER` is set to `systemd` — provides dependency-based boot ordering and native service management.
- `DL_DIR` and `SSTATE_DIR` are configured to `../downloads` and `../sstate-cache` (repo-relative, outside `build/`).
- Yocto Project sstate mirror and hash equivalence server are enabled for faster builds.
- `DISTRO_FEATURES` includes `rauc` for A/B update support.

## A/B Update System (RAUC)

The image uses RAUC for safe, atomic A/B partition updates with automatic rollback.

**Only the rootfs slots are in the A/B scheme.** A RAUC bundle (`RAUC_BUNDLE_SLOTS = "rootfs"`; `system.conf` defines only `slot.rootfs.0`/`.1`) writes the new kernel+rootfs to the inactive slot and swaps — nothing else. The **bootloader (`barebox.efi`) and `state.dtb` live on the ESP (partition 1), and the bootchooser state lives on partition 2; neither is touched by RAUC.** There is no `[slot.bootloader.*]` and no install hook. Consequence: a Yocto upgrade that bumps barebox (e.g. Whinlatter `2025.09` → Wrynose `2026.04`) rebuilds barebox into `tmp/deploy/` but that binary **never reaches a device via `rauc install`** — the device keeps running the barebox already on its ESP. The new barebox only lands on a full `.wic` flash (fresh SSD / disaster recovery), via the `bootimg-barebox-efi` wic plugin. This is deliberate: keeping the bootloader out of the rollback path means an OS update can't brick the bootloader. To intentionally update barebox on a running device you must reflash the ESP by hand (no automatic rollback — keep a SystemRescue USB handy).

### Partition Layout (8GB SSD)

| # | Label | Size | Type | Purpose |
|---|-------|------|------|---------|
| 1 | boot | 64 MiB | vfat | EFI System Partition (barebox.efi + state.dtb) |
| 2 | state | 1 MiB | raw | Barebox state backend (bootchooser persistent state) |
| 3 | rootfs-a | 1024 MiB | ext4 | Root filesystem slot A |
| 4 | rootfs-b | 1024 MiB | ext4 | Root filesystem slot B |
| 5 | data | 5100 MiB | ext4 | Persistent data (logs, config, certs) |

### Build Commands

```bash
source ./layers/openembedded-core/oe-init-build-env build
bitbake core-image-minimal        # image with A/B layout
bitbake futro-s920-bundle         # RAUC update bundle (.raucb)
```

### Initial Flash (from USB boot)

Boot the Futro S920 from a [SystemRescue](https://www.system-rescue.org/) USB stick, then:

```bash
# Decompress and write the image to the mSATA SSD
zstdcat core-image-minimal-futro-s920.wic.zst | dd of=/dev/sda bs=4M status=progress

# Fix GPT backup header (required after dd to a larger disk)
gdisk /dev/sda    # type 'w' then 'Y' to rewrite the partition table
```

### Update Workflow

**1. Build the RAUC bundle on the host:**

```bash
source ./layers/openembedded-core/oe-init-build-env build
bitbake futro-s920-bundle
```

The bundle is output to `build/tmp/deploy/images/futro-s920/futro-s920-bundle-futro-s920.raucb`.

**2. Transfer the bundle to the device:**

```bash
scp tmp/deploy/images/futro-s920/futro-s920-bundle-futro-s920.raucb root@<device-ip>:/tmp/update.raucb
```

**3. Install and reboot on the device:**

```bash
rauc status                       # check current slot status
rauc install /tmp/update.raucb    # install to inactive slot
reboot                            # boot into updated slot
rauc status mark-good             # confirm slot is good
```

### Certificates

Development CA and signing keys are in `layers/meta-futro-s920/files/rauc-keys/`. The CA cert (`ca.cert.pem`) is installed on the target as the keyring. The signing key (`development-1.key.pem`) stays on the build host.

### RAUC Pitfalls

**Slot `bootname` must match bootchooser target names:** In `system.conf`, each slot's `bootname` must exactly match the corresponding barebox bootchooser target name (e.g. `system0`, `system1` — as defined in `nv/bootchooser.targets`). Do NOT use arbitrary names like `A`/`B`. When RAUC queries barebox state, `last_chosen` returns the bootchooser target name; if no slot's `bootname` matches, RAUC fails with: `Failed to determine slot states: Did not find booted slot (matching 'system0')`.

**ESP must be mounted for `barebox-state` on x86:** The `barebox-state` userspace tool (dt-utils) reads the state DTB from the filesystem. On x86 EFI (no `/proc/device-tree`), it relies on `state.dtb` being accessible at `/boot/EFI/barebox/state.dtb`. The ESP must be mounted at `/boot` in fstab, otherwise `barebox-state` fails with: `Unable to read devicetree. No such file or directory`. RAUC depends on `barebox-state` to determine slot states.

**`state.dts` is the bootchooser-state ABI — keep it frozen across upgrades:** The on-disk format of the bootchooser state (partition 2) is defined *entirely* by `recipes-bsp/barebox/barebox/state.dts`, **not** by the version of barebox or `barebox-state`. Both the on-ESP barebox and the userland `barebox-state` (dt-utils) read the *same* `state.dtb` and derive the magic (`0x4d433230`), `backend-type`/`backend-storage-type`/`backend-stridesize`, and every variable's byte offset and type from it. This is what makes upgrades safe: a Yocto bump that moves barebox (e.g. Whinlatter's `2025.09` → Wrynose's `2026.04`) or `dt-utils` cannot write an incompatible state on its own, because neither tool carries an independent layout — and a RAUC update doesn't even reflash the ESP barebox (see the note at the top of "A/B Update System" — only the rootfs slots are in the A/B scheme). The format is magic- + CRC-guarded with redundant copies, so a mismatch fails *closed* (reader rejects on bad magic/CRC → bootchooser falls back to the compiled defaults and boots the default slot), never silent corruption.

Consequences for maintenance:

- **Treat any edit to `state.dts` as an ABI break.** Reordering/adding/removing variables, changing a `reg` offset, the `magic`, `backend-storage-type` (`direct`↔`circular`), or `backend-stridesize` changes the on-disk layout. An ESP barebox compiled against the old DTS and a new `state.dtb` (or vice versa) will disagree.
- **`state.dtb` only reaches the device on a full `.wic` flash**, via the `bootimg-barebox-efi` wic plugin — *not* via RAUC. So if you must change `state.dts`, you have to reflash the ESP (full flash, or a deliberate manual ESP update) and accept a one-time state reset; you cannot ship a state-layout change through a normal RAUC bundle.
- **`barebox-state` (dt-utils, from `meta-rauc`) only ever reads `state.dtb` and writes the raw backend partition** — it never rewrites `state.dtb`, so the layout source-of-truth cannot drift at runtime.
- **If you ever bump `dt-utils` in `meta-rauc`,** the only theoretical risk is a barebox state *format-version* bump while an old barebox stays on the ESP. These are rare and backward-compatible, and the magic/CRC guard keeps them fail-closed — but keep barebox, `barebox-state`, and `state.dtb` in the same era when you do a full reflash. Keep the offsets stable (the comment header in `state.dts` already says so).

### Notes

- Device paths in `system.conf` use `PARTUUID`/`by-partuuid` references, so the image works on both real hardware (`/dev/sda`) and QEMU with virtio (`/dev/vda`) without changes.
- `rauc-mark-good.service` systemd unit auto-marks the booted slot as good after successful boot.
- The `meta-rauc` warning about `meta-filesystems` can be ignored (only needed for casync/FUSE).

## Static Configuration & Persistent State

All device configuration is managed statically in this repo and baked into the image at build time — there is no on-device configuration. Anything mutable in the rootfs would be wiped on the next RAUC slot swap, so the model is: configuration is static-from-recipe; operational state lives on `/data`.

### Out-of-tree secrets

Secrets baked into the image (password hash, SSH host keys, machine-id) live under `layers/meta-futro-s920/files/secrets/` which is **gitignored** (only `README.md` is tracked). Recipes read these at parse / `do_install` time via the `SECRETS_DIR` variable defined in `meta-futro-s920/conf/layer.conf`. Missing files trigger `bb.fatal` with the expected path — see `files/secrets/README.md` for generation commands.

| Secret | Consumer | Generated with |
|---|---|---|
| `pplr.hash` | `core-image-minimal.bbappend` (extrausers) | `mkpasswd -m sha512crypt -R 500000` |
| `machine-id` | `base-files_%.bbappend` | `python3 -c "import uuid; print(uuid.uuid4().hex)"` |
| `ssh/ssh_host_{ed25519,rsa}_key{,.pub}` | `openssh_%.bbappend` | `ssh-keygen -t {ed25519,rsa} -N '' -f ...` |
| `netdata/stream_api_key` | `netdata_%.bbappend` (streaming parent) | `uuidgen` — **same UUID must also live in each streaming AP's `aps/secrets/netdata/stream_api_key`** |

To rotate any secret: regenerate the file, rebuild, deploy via RAUC. Hostname (`home-router`) is *not* a secret and is committed at `recipes-core/base-files/base-files/hostname`.

### Persistent operational state on /data

Operational state is bind-mounted from `/data` so it survives A/B updates. They are *not* configuration — they're state generated at runtime that we want to keep across slot swaps.

| Bind mount | Purpose |
|---|---|
| `/data/var/log/journal` → `/var/log/journal` | Persistent systemd journal (`Storage=persistent` via journald drop-in) |
| `/data/var/lib/systemd/network` → `/var/lib/systemd/network` | DHCP server leases for connected LAN clients |
| `/data/var/cache/netdata` → `/var/cache/netdata` | netdata runtime cache |
| `/data/var/lib/netdata` → `/var/lib/netdata` | netdata persistent metrics database |
| `/data/home/pplr` → `/home/pplr` | `pplr` user's home (shell history, dotfiles); seeded from the rootfs skeleton on first boot |

Source dirs on `/data` are created on first mount by `futro-data-prep.service` (oneshot, ordered between `data.mount` and the bind mounts via `x-systemd.requires=` in fstab). Provided by the `futro-persistent-state` recipe in `recipes-core/futro-persistent-state/`.

### Configuration Pitfalls

**`passwd-expire` is poison with A/B updates:** the `extrausers` directive `passwd-expire <user>;` forces a password change on first login. With a static-rootfs A/B model the new hash lives only in the active slot's `/etc/shadow` and is wiped on the next RAUC swap, so the user gets prompted to change password on every update. Solution: bake a real hash via the secrets mechanism and **do not** use `passwd-expire`.

**Don't mask a systemd unit with a `/dev/null` symlink at do_install — drop it from the build instead:** chrony is the sole NTP daemon, so `systemd-timesyncd` must not also run (two clients racing for the clock causes spurious time jumps). The tempting fix — `ln -sf /dev/null ${D}${sysconfdir}/systemd/system/systemd-timesyncd.service` in a bbappend — *works at runtime but breaks the image build*: at `do_rootfs`, `systemctl preset-all` enumerates the masked symlink and prints `Failed to preset all unit: Unit …/systemd-timesyncd.service is masked`. preset-all ignores it and continues, but OE's `log_check` greps the rootfs log for failure patterns, matches `Failed`, and fails `do_rootfs`. Fix: remove the unit from the systemd build entirely with `PACKAGECONFIG:remove = "timesyncd"` in `recipes-core/systemd/systemd_%.bbappend` — no binary, no unit, nothing for preset-all to trip on. (General rule: prefer omitting an unwanted systemd service from its recipe over masking it in the rootfs.)

**fstab bind mounts need ordering:** `data.mount` and any `/data/...` bind mount in fstab both target `local-fs.target`. Without explicit ordering the bind mount can fire before the source directory exists. Use `x-systemd.requires=futro-data-prep.service` on the bind mount entry — that pulls in the oneshot which creates source dirs after `data.mount` and before journald starts.

**`useradd -p` hash dollars get eaten by shell expansion:** `extrausers.bbclass` opens its task function with `user_group_settings="${EXTRA_USERS_PARAMS}"` — a *double-quoted* shell assignment. Inside that, the single quotes around `'${PPLR_PASSWD_HASH}'` in the recipe are inert: bash sees the SHA-512 crypt hash (`$6$rounds=…$<salt>$<hash>`) as a sequence of `$NAME` references and substitutes empty strings, mangling the hash before `useradd` ever runs. Symptom: SSH refuses the (correct) password and `/etc/shadow` contains a truncated hash like `pplr:=500000.…`. Fix: pre-escape `$` in Python before bitbake substitution — `read_secret(...).replace('$', r'\$')` — so the literal `\$` in the generated shell line survives the assignment as a bare `$`.

**`set -u` breaks `oe-init-build-env`:** sourcing the init script under bash `set -u` (nounset) dies on `BBSERVER: unbound variable` (line 29) before bitbake reaches the PATH. Use `set -eo pipefail` (no `u`) when scripting bitbake invocations.

**Files added to `/etc/ssh/` via the openssh bbappend land in `openssh`, not `openssh-sshd`:** OE-core's `openssh.inc` defines `FILES:${PN}-sshd` as an *explicit* list (`${sbindir}/sshd`, `${sysconfdir}/ssh/sshd_config`, `moduli`, `sshd_check_keys`, …) — not a `${sysconfdir}/ssh/*` glob. So any extra files we install under `/etc/ssh/` in `openssh_%.bbappend`'s `do_install:append()` fall through to the `openssh` meta-package's catch-all. We install only `openssh-sshd` in `core-image-minimal.bbappend`, so without an explicit `FILES:${PN}-sshd +=` reclaim those files are silently dropped from the rootfs. Symptom: SSH host keys baked from `SECRETS_DIR/ssh/` go missing, `sshdgenkeys.service` regenerates fresh random keys on the first boot of every new RAUC slot, and the device's SSH fingerprint flips on every update; same trap for any `sshd_config.d/*.conf` drop-in. Fix: add `FILES:${PN}-sshd += "${sysconfdir}/ssh/ssh_host_* ${sysconfdir}/ssh/sshd_config.d/<your>.conf"` in the bbappend so the explicit ordering of `PACKAGES =+ "… ${PN}-sshd …"` claims them before the catch-all.

**Bake *all* host key types `sshdgenkeys` knows about, not just the ones you like:** `sshd_check_keys` (shipped by OE-core's openssh) unconditionally generates `ed25519`, `rsa`, **and `ecdsa`** keys at first boot if any are missing — it doesn't care which algorithms `sshd_config` actually serves. If the bbappend only bakes ed25519 + rsa, the ecdsa key gets freshly generated on every new RAUC slot and the device's ECDSA fingerprint flips on every update. Whether the user *sees* the flip depends on which `HostKeyAlgorithms` their ssh client prefers (OpenSSH order varies by distro). Symptom: ed25519/rsa fingerprints match `secrets/ssh/` across flashes (`Apr 5 2011` timestamps from SOURCE_DATE_EPOCH), but `ssh` still warns `REMOTE HOST IDENTIFICATION HAS CHANGED` because the negotiated key is ECDSA — and `ls -la /etc/ssh/` shows only the ecdsa pair with a fresh boot-time mtime. Fix: bake ecdsa alongside ed25519 + rsa in `secrets/ssh/` and add it to `SRC_URI`, `do_install:append`, and `FILES:${PN}-sshd +=` so `sshdgenkeys` becomes a true no-op.

## Networking

systemd-networkd-managed three-NIC setup: `wan0` (PCI `04:00.0`) faces the Freebox, `lan0` (`03:00.0`) and `lan1` (`05:00.0`) are bridged into `br-lan` (10.0.0.1/24). DHCPv4 server runs on `br-lan`; DNS is systemd-resolved with DoT to Cloudflare. Config files live in `recipes-core/network/files/` and are installed by `futro-network-conf`.

### Freebox IPTV (VLAN 100)

A Freebox Revolution tags **player↔server traffic with VLAN 100** on the same Ethernet wire it uses for WAN data. The router acts as a transparent L2 switch for that VLAN between the WAN port and both LAN ports, so a Freebox Player plugged into either `lan0` or `lan1` reaches the Freebox Server upstream.

- `wan0.100`, `lan0.100`, `lan1.100` — VLAN sub-interfaces (`Kind=vlan`, `Id=100`), declared via `VLAN=` on each parent's `.network`.
- `br-iptv` — dedicated **L2-only** bridge (`LinkLocalAddressing=no`, `IPv6AcceptRA=no`) with the three sub-interfaces as members. No IP stack on the router for this VLAN.

The kernel demuxes tagged frames at the parent NIC: tag 100 is stolen into the `.100` sub-interface (and from there into `br-iptv`); untagged frames stay on the parent and continue through `br-lan`. So `lan0` / `lan1` carry untagged LAN data **and** tagged IPTV on the same wire — no dedicated port required.

Why a separate `br-iptv` instead of enabling bridge-VLAN-filtering on `br-lan`: making `wan0` a trunk member of `br-lan` would force migrating its DHCP/RA L3 stack onto a VLAN sub-interface and entangle the WAN with the LAN bridge. Keeping IPTV in its own bridge keeps the two concerns orthogonal and the WAN config untouched.

### IoT VLAN (VLAN 30)

A third, untrusted broadcast domain for IoT devices on VLAN 30. Unlike IPTV (L2 ferry between WAN and LAN), IoT is an **L3-routed** downstream-only VLAN — it never touches wan0 as L2; egress is via routing/NAT.

- `lan0.30`, `lan1.30` — VLAN sub-interfaces, declared via `VLAN=` on each parent's `.network`. Same kernel-level demux as IPTV: tag 30 is stolen into the `.30` sub-interface at parent ingress, untagged frames still flow into `br-lan`. So lan0/lan1 carry untagged LAN data **and** tagged IPTV (VLAN 100) **and** tagged IoT (VLAN 30) on the same wire.
- `br-iot` — full L3 bridge, mirror of br-lan: `10.0.30.1/24` + `2a01:e0a:97f:5433::1/64`, `IPMasquerade=ipv4` (NAT44 to WAN), `IPv6SendRA=yes` + `[IPv6Prefix]` (native v6 to WAN), `DHCPServer=yes` (pool `10.0.30.100`–`10.0.30.254`), advertises itself as RDNSS.
- **No wan0.30 sub-interface and no trunk to wan0** — IoT traffic egresses via routing and SNAT on wan0, not via L2 bridging like IPTV.

**Trust model (firewall):** asymmetric. LAN can freely initiate to IoT (so a LAN laptop can reach a smart-home hub); IoT cannot initiate to LAN — implicitly denied by the `forward` chain's default-drop, with conntrack `established,related` carrying return traffic for LAN-initiated sessions. IoT → router-local services is narrowed to **DHCPv4 (udp/67) + DNS (udp/53 + tcp/53)** only; SSH, netdata, and any future admin port fall through to the input chain's default-drop. IoT → WAN is allowed for both IPv4 (NATted) and IPv6 (native, via the second delegated /64).

**Hardware prerequisite:** downstream must be VLAN-aware — a managed switch tagging IoT-port frames with VLAN 30, or a VLAN-capable AP with an IoT SSID bound to VLAN 30. A dumb switch delivers IoT frames untagged on lan0/lan1, defeating the isolation.

### IPv6

The router is behind a Freebox that **statically delegates two `/64` prefixes** to it: `2a01:e0a:97f:5432::/64` for the trusted LAN and `2a01:e0a:97f:5433::/64` for the untrusted IoT VLAN. Configure on the Freebox side via *Paramètres de la Freebox → Configuration IPv6 → Délégation de préfixe*. Each row takes a **Préfixe IPv6 délégué** (the `/64`) and a **Prochaine route (Next Hop)** — the IPv6 address of the device receiving the delegation, which is the router's `wan0` SLAAC GUA (learned from the Freebox RA; check with `ip -6 addr show wan0 scope global`). Both delegation rows must use the same Next Hop. The router-side IPv6 model is fully static — matches the project's static-config philosophy:

| Interface | IPv6 |
|---|---|
| `wan0` | SLAAC (Freebox RA) — `IPv6AcceptRA=yes`, `UseDNS=false` so resolved keeps its DoT setup |
| `br-lan` | Static `2a01:e0a:97f:5432::1/64`, RA emission via `IPv6SendRA=yes` + `[IPv6Prefix]`, advertises itself as RDNSS |
| `br-iot` | Static `2a01:e0a:97f:5433::1/64`, same RA emission pattern as br-lan, advertises itself as RDNSS |
| Forwarding | `IPv4Forwarding=yes` + `IPv6Forwarding=yes` per-interface on wan0, br-lan, and br-iot |
| NAT | IPv4 only (`IPMasquerade=ipv4` on **br-lan** and **br-iot** — see Networking Pitfalls for why it's on the LAN side, not wan0). IPv6 is end-to-end — clients carry public GUAs from their respective delegated /64. |

systemd-resolved listens for DNS on `10.0.0.1`, `2a01:e0a:97f:5432::1`, `10.0.30.1`, and `2a01:e0a:97f:5433::1` (see `resolved-router.conf`).

### Firewall (nftables)

Stateful inet-family firewall shipped by the `futro-firewall` recipe (`recipes-extended/firewall/`). The systemd unit runs `Before=network-pre.target`, so the policy is in effect before any interface comes up.

**Trust model:** LAN is trusted, WAN is hostile, IoT is untrusted.
- `input`: drop default; accept lo, ICMP, ICMPv6, anything from `br-lan`, DHCPv4/DNS only from `br-iot`, and DHCPv4 client replies on `wan0`.
- `forward`: drop default; accept established/related (return path for both v4 and v6), `br-lan → wan0` (LAN egress), `br-lan → br-iot` (LAN-initiated to IoT — asymmetric trust), and `br-iot → wan0` (IoT egress). `br-iot → br-lan` is implicitly dropped.
- `output`: accept default.

### Networking Pitfalls

**`IPv6AcceptRA=` defaults flip with forwarding:** systemd-networkd defaults `IPv6AcceptRA=` to `no` whenever IPv6 forwarding is enabled on an interface — the assumption is that routers are usually not also CPE clients. Our `wan0` is both: it forwards LAN traffic out and learns its default IPv6 route from the Freebox's RA. So `IPv6AcceptRA=yes` must be set **explicitly** on `wan0`; relying on the default silently kills outbound IPv6.

**Don't `flush ruleset` in the firewall config:** systemd-networkd installs its own `ip nat` table for `IPMasquerade=ipv4`. A blanket `flush ruleset` in `nftables.conf` would wipe NAT, breaking IPv4 internet access until networkd is restarted. Use the atomic `table inet filter` / `delete table inet filter` idiom to replace only our own table, so the firewall can be reloaded at runtime without touching networkd's NAT.

**Allow ICMPv6 unconditionally on input:** ICMPv6 carries Neighbor Discovery, Router Advertisements, and PMTUD — drop it and IPv6 reachability silently dies (no neighbor cache, no PMTUD, RA from Freebox doesn't update default route). A single `ip6 nexthdr icmpv6 accept` covers it.

**`meta-networking` must be in LAYERDEPENDS:** the `futro-firewall` recipe RDEPENDS on `nftables` which comes from `meta-openembedded/meta-networking`. The collection name is `networking-layer` (see its `layer.conf`). Without the LAYERDEPENDS entry, bitbake parses fine but the layer-compat check warns; with it, the dependency is explicit.

**`IPMasquerade=ipv4` masquerades the interface's *own* subnet, not its egress:** unlike the iptables idiom `-t nat -A POSTROUTING -o wan0 -j MASQUERADE`, systemd-networkd's `IPMasquerade=ipv4` adds *this interface's* network prefix (the address masked by `prefixlen`) into the `masq_saddr` set inside its private `io.systemd.nat` table — see `src/network/networkd-address.c:668-688` in the systemd source. The postrouting rule (`ip saddr @masq_saddr masquerade`) then SNATs any packet whose **source** matches the set. So for home-router NAT (LAN→WAN), the directive belongs on **`br-lan`** (the LAN subnet we want masqueraded), not on `wan0`. If put on `wan0`, the set ends up containing the Freebox-side DHCP subnet (e.g. `192.168.0.0/24`) and LAN clients (`10.0.0.0/24`) never match the rule — packets leave `wan0` with their original private source and the internet drops them. Symptom: laptop on LAN gets a lease, can ping the router, can't reach `8.8.8.8`; on the router, `nft list table ip io.systemd.nat` shows `masq_saddr` containing the WAN's subnet instead of the LAN's.

**systemd-networkd's DHCPv4 server receive path traverses netfilter; the send path does not:** `sd-dhcp-server` opens a regular UDP socket bound to the interface for inbound DHCPDISCOVER/REQUEST (`src/libsystemd-network/sd-dhcp-server.c:1325`) but sends replies via an `AF_PACKET` raw socket that bypasses netfilter. Consequence: any **untrusted** bridge with the DHCP server enabled needs an explicit `iifname "br-xxx" udp dport 67 accept` rule on `input`, or clients never get leases (silent drop, no log line beyond the chain's drop counter). `br-lan` doesn't need it because the catch-all `iifname "br-lan" accept` already covers DHCP; `br-iot` does because the trust model there is default-drop + narrow allow. No `output` rule is ever needed for DHCP replies.

**IPv6 forwarding requires the *global* sysctl, IPv4 doesn't:** the two address families are not symmetric in the kernel. `ip_route_input_slow()` (`net/ipv4/route.c`) checks `IN_DEV_FORWARD(in_dev)` — i.e. *per-interface only* — so per-interface `IPv4Forwarding=yes` on `wan0` + `br-lan` is sufficient for IPv4. But `ip6_forward()` (`net/ipv6/ip6_output.c:513`) checks `net->ipv6.devconf_all->forwarding` *first* and drops the packet immediately if it's 0 — per-interface flags are not consulted at all when the global is 0. Setting `IPv6Forwarding=yes` in a `.network` file only writes `/proc/sys/net/ipv6/conf/<iface>/forwarding`; the global is written by `manager_set_ip_forwarding()` (`src/network/networkd-sysctl.c:200`), which is only invoked when the global setting is present in **`networkd.conf`**'s `[Network] IPv6Forwarding=`. We ship that as a `networkd.conf.d/router.conf` drop-in via `futro-network-conf`. Symptom if missing: LAN clients get GUAs and a default route, but every forwarded IPv6 packet is silently dropped by the router; the router itself can still `ping -6` the internet because locally-generated packets bypass `ip6_forward()`.

## Barebox Bootloader

Barebox runs as an EFI payload, managing A/B slot selection via the bootchooser framework.

### Key Files

- `recipes-bsp/barebox/barebox.bbappend` — builds barebox with `efi_defconfig`, compiles `state.dtb`, deploys both
- `recipes-bsp/barebox/barebox/bootchooser.cfg` — kconfig fragment enabling bootchooser + state
- `recipes-bsp/barebox/barebox/state.dts` — state description (compiled to DTB, placed on ESP)
- `recipes-bsp/barebox/barebox/env/boot/system0` / `system1` — boot scripts for each slot
- `recipes-bsp/barebox/barebox/env/nv/bootchooser.*` — bootchooser nv variables

### Barebox Pitfalls

**Global variable syntax in boot scripts:** Use `global foo="value"` (command with space), NOT `global.foo="value"` (dot assignment). The dot syntax is a direct variable assignment that silently fails if the parameter doesn't exist yet — causing arguments like `console=` to be missing from the kernel command line with no error.

**State backend wiring (state.dts):** Use a `fixed-partitions` node with a `partuuid` property pointing at the state partition's GPT partition entry UUID. Do NOT use `barebox,storage-by-uuid` (`CONFIG_STORAGE_BY_ALIAS`) — it creates link cdevs that trigger `BUG_ON(cdev->link)` in `cdev_readlink()` (fs/devfs-core.c:62) when a link-to-link chain forms. The `fixed-partitions` + `partuuid` approach makes `__of_cdev_find()` (drivers/of/of_path.c:65-71) call `cdev_by_partuuid()` directly, bypassing the link mechanism entirely. The `partuuid` must match the `--uuid=` of the state partition in the wks file.

**bootchooser.state_prefix format:** Must be `<state_device>.<inner_prefix>` (e.g. `state.bootstate`). Barebox splits on the first `.` at `common/bootchooser.c:371` — left side is passed to `state_by_name()`, right side is the variable prefix within the state. If the state DTS node is named `state` and the container is `bootstate`, the prefix must be `state.bootstate`.

**Kconfig fragments:** Symbols are merged via `merge_config.sh` on top of `BAREBOX_CONFIG` (e.g. `efi_defconfig`). Always verify symbols exist in the barebox source Kconfig before adding — non-existent symbols (e.g. `CONFIG_EFI_DEVICETREE`) are silently ignored. Check the built config: `grep SYMBOL tmp/work/.../barebox/.../build/.config`.

### Hardware

Onboard ethernet device: 0000:05:00.0
PCI ethernet board port 1: 0000:03:00.0
PCI ethernet board port 2: 0000:04:00.0