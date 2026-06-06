COMPATIBLE_MACHINE:futro-s920 = "futro-s920"
KMACHINE:futro-s920 ?= "common-pc-64"

FILESEXTRAPATHS:prepend := "${THISDIR}:"
SRC_URI += "file://futro-s920-gfx.cfg"
SRC_URI += "file://futro-s920-netfilter.cfg"
