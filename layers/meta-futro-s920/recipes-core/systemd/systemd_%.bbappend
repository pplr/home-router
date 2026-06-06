PACKAGECONFIG:append = " openssl"

# Drop systemd-timesyncd entirely: chrony is our sole NTP client/server, and
# two NTP clients racing for the system clock causes spurious time jumps.
# Removing the PACKAGECONFIG omits the binary and unit from the build, which is
# cleaner than masking it on the target — a /dev/null mask symlink in
# /etc/systemd/system makes `systemctl preset-all` (run at do_rootfs) emit
# "Failed to preset all unit: ... is masked", which trips OE's log_check and
# fails the image build.
PACKAGECONFIG:remove = "timesyncd"
