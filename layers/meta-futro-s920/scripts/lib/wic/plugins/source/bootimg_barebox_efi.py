#
# SPDX-License-Identifier: MIT
#
# WIC source plugin to create an EFI System Partition with barebox.
#

import logging
import os
import re

from wic import WicError
from wic.pluginbase import SourcePlugin
from wic.misc import exec_cmd, exec_native_cmd, get_bitbake_var, BOOTDD_EXTRA_SPACE

logger = logging.getLogger('wic')


class BootimgBareboxEFIPlugin(SourcePlugin):
    """
    Create EFI boot partition populated with a barebox EFI binary
    and optional extra files (e.g. state.dtb for bootchooser).
    """

    name = 'bootimg_barebox_efi'

    @classmethod
    def do_prepare_partition(cls, part, source_params, creator, cr_workdir,
                             oe_builddir, bootimg_dir, kernel_dir,
                             rootfs_dir, native_sysroot):
        if not kernel_dir:
            kernel_dir = get_bitbake_var("DEPLOY_DIR_IMAGE")
            if not kernel_dir:
                raise WicError("Couldn't find DEPLOY_DIR_IMAGE, exiting")

        # Derive EFI boot image name from TARGET_SYS (EFI_BOOT_IMAGE is not
        # in WICVARS, so we resolve it the same way bootimg_efi.py does).
        target = get_bitbake_var("TARGET_SYS")
        if not target:
            raise WicError("TARGET_SYS is not set, exiting")

        if re.match("x86_64", target):
            efi_boot_image = "bootx64.efi"
        elif re.match('i.86', target):
            efi_boot_image = "bootia32.efi"
        elif re.match('aarch64', target):
            efi_boot_image = "bootaa64.efi"
        elif re.match('arm', target):
            efi_boot_image = "bootarm.efi"
        else:
            raise WicError("Unsupported target for barebox EFI: %s" % target)

        hdddir = "%s/hdd/boot" % cr_workdir

        # Create ESP directory structure and install barebox EFI binary
        exec_cmd("install -d %s/EFI/BOOT" % hdddir)
        exec_cmd("install -m 0644 %s/%s %s/EFI/BOOT/%s"
                 % (kernel_dir, efi_boot_image, hdddir, efi_boot_image))
        logger.debug("Installed barebox EFI binary: %s", efi_boot_image)

        # Install barebox state DTB if requested
        state_dtb = source_params.get('state-dtb')
        if state_dtb:
            exec_cmd("install -d %s/EFI/barebox" % hdddir)
            exec_cmd("install -m 0644 %s/%s %s/EFI/barebox/%s"
                     % (kernel_dir, state_dtb, hdddir, state_dtb))
            logger.debug("Installed barebox state DTB: %s", state_dtb)

        # Calculate partition size
        du_cmd = "du -bks %s" % hdddir
        out = exec_cmd(du_cmd)
        blocks = int(out.split()[0])

        extra_blocks = part.get_extra_block_count(blocks)
        if extra_blocks < BOOTDD_EXTRA_SPACE:
            extra_blocks = BOOTDD_EXTRA_SPACE
        blocks += extra_blocks

        if blocks < part.fixed_size:
            blocks = part.fixed_size

        # Create and populate vfat image
        bootimg = "%s/boot.img" % cr_workdir
        label = part.label if part.label else "ESP"

        dosfs_cmd = "mkdosfs -v -n %s -i %s -C %s %d" % \
                    (label, part.fsuuid, bootimg, blocks)
        exec_native_cmd(dosfs_cmd, native_sysroot)

        mcopy_cmd = "mcopy -v -p -i %s -s %s/* ::/" % (bootimg, hdddir)
        exec_native_cmd(mcopy_cmd, native_sysroot)

        exec_cmd("chmod 644 %s" % bootimg)

        du_cmd = "du --apparent-size -Lks %s" % bootimg
        out = exec_cmd(du_cmd)
        part.size = int(out.split()[0])
        part.source_file = bootimg
