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
# NIC PCI topology mirrors real Futro S920 hardware:
#   bus 3 (slot 0x3) → lan0  (PCI port 1: 0000:03:00.0)
#   bus 4 (slot 0x4) → wan0  (PCI port 2: 0000:04:00.0)
#   bus 5 (slot 0x5) → lan1  (Onboard:    0000:05:00.0)
# Slots 1–2 are empty root ports so bus numbering starts at 1.
exec qemu-system-x86_64 \
    -machine q35 \
    -cpu Nehalem \
    -m "$MEMORY" \
    -drive file="$WIC_IMG",format=raw,if=virtio \
    -bios "$OVMF_FW" \
    -vga std \
    -serial mon:stdio \
    -device pcie-root-port,id=rp1,bus=pcie.0,addr=0x2,chassis=1 \
    -device pcie-root-port,id=rp2,bus=pcie.0,addr=0x3,chassis=2 \
    -device pcie-root-port,id=rp3,bus=pcie.0,addr=0x4,chassis=3 \
    -device pcie-root-port,id=rp4,bus=pcie.0,addr=0x5,chassis=4 \
    -device pcie-root-port,id=rp5,bus=pcie.0,addr=0x6,chassis=5 \
    -netdev user,id=netwan,hostfwd=tcp::"$SSH_PORT"-:22 \
    -device virtio-net-pci,netdev=netwan,bus=rp4,addr=0x0 \
    -device virtio-net-pci,bus=rp3,addr=0x0 \
    -device virtio-net-pci,bus=rp5,addr=0x0
