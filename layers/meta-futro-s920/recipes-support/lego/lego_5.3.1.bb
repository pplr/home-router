SUMMARY = "lego (prebuilt) — ACME client used for the APs' LuCI certificates"
DESCRIPTION = " \
    Ships the upstream prebuilt linux-amd64 lego binary. The router uses it to \
    obtain one Let's Encrypt certificate per OpenWrt AP via the DNS-01 \
    challenge against the OVH-hosted zone — the only challenge type that works \
    here, since the APs are on RFC1918 addresses with no inbound path from the \
    internet. Issuance is driven from a CSR baked into each AP's image, so the \
    matching private key never leaves the AP (see futro-ap-certs). Prebuilt \
    rather than built-from-source (a large Go tree); pinned by sha256. \
"
HOMEPAGE = "https://go-acme.github.io/lego/"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "https://github.com/go-acme/lego/releases/download/v${PV}/lego_v${PV}_linux_amd64.tar.gz"
SRC_URI[sha256sum] = "b3c71b122ee1947eacfe0b809b955647f6377239fe4bfc49f73b1a091ae1252a"

S = "${UNPACKDIR}"

COMPATIBLE_HOST = "x86_64.*-linux"

# Prebuilt, statically-linked Go binary: don't try to strip it or split debug.
INHIBIT_PACKAGE_STRIP = "1"
INHIBIT_PACKAGE_DEBUG_SPLIT = "1"
INHIBIT_SYSROOT_STRIP = "1"
INSANE_SKIP:${PN} += "already-stripped ldflags"

do_configure[noexec] = "1"
do_compile[noexec] = "1"

# The release tarball extracts flat (lego, LICENSE, CHANGELOG.md).
do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${UNPACKDIR}/lego ${D}${bindir}/lego
}

FILES:${PN} = "${bindir}/lego"
