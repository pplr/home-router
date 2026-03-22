#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

IMAGE_DIR="$REPO_DIR/build/tmp/deploy/images/futro-s920"
WIC_ZST="$IMAGE_DIR/core-image-minimal-futro-s920.rootfs.wic.zst"
WIC_IMG="$IMAGE_DIR/core-image-minimal-futro-s920.rootfs.wic"

SSH_PORT="${SSH_PORT:-2222}"
MEMORY="${MEMORY:-4096}"

# Decompress the WIC image if needed
if [ ! -f "$WIC_IMG" ]; then
    if [ ! -f "$WIC_ZST" ]; then
        echo "Error: WIC image not found at $WIC_ZST" >&2
        echo "Build it first: source layers/openembedded-core/oe-init-build-env build && bitbake core-image-minimal" >&2
        exit 1
    fi
    echo "Decompressing $WIC_ZST ..."
    zstd -d "$(readlink -f "$WIC_ZST")" -o "$WIC_IMG"
fi

# Locate OVMF EFI firmware
for ovmf in /usr/share/OVMF/OVMF_CODE.fd /usr/share/ovmf/OVMF.fd /usr/share/edk2/ovmf/OVMF_CODE.fd; do
    if [ -f "$ovmf" ]; then
        OVMF_FW="$ovmf"
        break
    fi
done
if [ -z "${OVMF_FW:-}" ]; then
    echo "Error: OVMF firmware not found. Install it:" >&2
    echo "  Debian/Ubuntu: sudo apt install ovmf" >&2
    echo "  Fedora:        sudo dnf install edk2-ovmf" >&2
    exit 1
fi

echo "Starting QEMU (SSH on host port $SSH_PORT) ..."
exec qemu-system-x86_64 \
    -machine q35 \
    -cpu Nehalem \
    -m "$MEMORY" \
    -drive file="$WIC_IMG",format=raw,if=virtio \
    -bios "$OVMF_FW" \
    -nographic \
    -net nic,model=virtio \
    -net user,hostfwd=tcp::"$SSH_PORT"-:22
