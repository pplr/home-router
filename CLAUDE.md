# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Minimal Yocto Whinlatter (5.3) image for a Fujitsu Futro S920 home router.

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
  - `openembedded-core` — core metadata and `oe-init-build-env` script (git submodule, `whinlatter` branch)
  - `meta-yocto` — Poky distro policy (`meta-poky`) and BSP (`meta-yocto-bsp`) (git submodule, `whinlatter` branch)
  - `bitbake` — build engine (git submodule, `whinlatter` branch)
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

To rotate any secret: regenerate the file, rebuild, deploy via RAUC. Hostname (`home-router`) is *not* a secret and is committed at `recipes-core/base-files/base-files/hostname`.

### Persistent operational state on /data

Two pieces of operational state are bind-mounted from `/data` so they survive A/B updates. They are *not* configuration — they're state generated at runtime that we want to keep across slot swaps.

| Bind mount | Purpose |
|---|---|
| `/data/var/log/journal` → `/var/log/journal` | Persistent systemd journal (`Storage=persistent` via journald drop-in) |
| `/data/var/lib/systemd/network` → `/var/lib/systemd/network` | DHCP server leases for connected LAN clients |

Source dirs on `/data` are created on first mount by `futro-data-prep.service` (oneshot, ordered between `data.mount` and the bind mounts via `x-systemd.requires=` in fstab). Provided by the `futro-persistent-state` recipe in `recipes-core/futro-persistent-state/`.

### Configuration Pitfalls

**`passwd-expire` is poison with A/B updates:** the `extrausers` directive `passwd-expire <user>;` forces a password change on first login. With a static-rootfs A/B model the new hash lives only in the active slot's `/etc/shadow` and is wiped on the next RAUC swap, so the user gets prompted to change password on every update. Solution: bake a real hash via the secrets mechanism and **do not** use `passwd-expire`.

**fstab bind mounts need ordering:** `data.mount` and any `/data/...` bind mount in fstab both target `local-fs.target`. Without explicit ordering the bind mount can fire before the source directory exists. Use `x-systemd.requires=futro-data-prep.service` on the bind mount entry — that pulls in the oneshot which creates source dirs after `data.mount` and before journald starts.

**`set -u` breaks `oe-init-build-env`:** sourcing the init script under bash `set -u` (nounset) dies on `BBSERVER: unbound variable` (line 29) before bitbake reaches the PATH. Use `set -eo pipefail` (no `u`) when scripting bitbake invocations.

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