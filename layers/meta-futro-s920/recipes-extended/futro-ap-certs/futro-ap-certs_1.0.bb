SUMMARY = "Centralised Let's Encrypt certificates for the OpenWrt APs' LuCI"
DESCRIPTION = " \
    Obtains one Let's Encrypt certificate per OpenWrt AP via the DNS-01 \
    challenge (lego + the OVH provider) and pushes the signed certificate to \
    each AP over SSH. Issuance is centralised here because DNS-01 needs \
    zone-wide OVH API credentials, which should exist in exactly one place. \
    Each AP's private key is generated at image-build time and baked into its \
    own squashfs — the router only ever handles the CSR and the signed \
    certificate, both public. Driven by a daily systemd timer. \
"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

SRC_URI = " \
    file://futro-ap-certs.sh \
    file://futro-ap-certs.service \
    file://futro-ap-certs.timer \
"

S = "${UNPACKDIR}"

inherit systemd

# Only the timer is enabled: the service carries no [Install] section and
# is activated by the timer (or by hand for a forced cycle).
SYSTEMD_SERVICE:${PN} = "futro-ap-certs.timer"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

# lego does the ACME work; openssh-ssh is the client used for the push
# (the image otherwise installs only openssh-sshd, the server).
RDEPENDS:${PN} = "lego openssh-ssh"

CONF_DIR = "${sysconfdir}/futro-ap-certs"

do_install() {
    install -d ${D}${libexecdir}
    install -m 0755 ${UNPACKDIR}/futro-ap-certs.sh ${D}${libexecdir}/futro-ap-certs.sh

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${UNPACKDIR}/futro-ap-certs.timer \
        ${D}${systemd_system_unitdir}/futro-ap-certs.timer
    # ACME contact address comes from config.toml, so the unit stays in
    # sync with the rest of the declarative config.
    sed -e 's|@ACME_EMAIL@|${ACME_EMAIL}|g' \
        ${UNPACKDIR}/futro-ap-certs.service \
        > ${D}${systemd_system_unitdir}/futro-ap-certs.service
    chmod 0644 ${D}${systemd_system_unitdir}/futro-ap-certs.service

    install -d -m 0755 ${D}${CONF_DIR}
    install -d -m 0755 ${D}${CONF_DIR}/csr

    # lego's store; the real one is a bind mount from /data (see fstab).
    # This is the pre-mount placeholder.
    install -d -m 0700 ${D}${localstatedir}/lib/lego
}

# Generate the declarative bits from config.toml + install the secret and
# per-AP material. Attached as a postfunc rather than `python
# do_install:append` because mixing shell `do_X:append` and `python
# do_X:append` text-merges both bodies into the base shell task, breaking
# the shell dep parser.
python do_install_ap_certs() {
    import pathlib, sys, os
    sys.path.insert(0, d.getVar("ROUTERBUILD_ROOT"))
    from routerbuild.config import NetworkConfig
    from routerbuild.render import write_ap_cert_list

    def read_secret(base_var, relpath, hint):
        """Read a secret, failing with the exact path the user must populate."""
        path = os.path.join(d.getVar(base_var), relpath)
        if not os.path.exists(path):
            bb.fatal("Missing secret '%s' (expected at %s). %s" % (relpath, path, hint))
        with open(path) as f:
            return f.read()

    ROUTER_HINT = "See layers/meta-futro-s920/files/secrets/README.md."
    AP_HINT = "Run aps/gen-ap-tls.py; see aps/secrets/README.md."

    def install_file(target, content, mode):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        os.chmod(target, mode)

    cfg = NetworkConfig.load(pathlib.Path(d.getVar("CONFIG_TOML")))
    conf_dir = pathlib.Path(d.getVar("D") + d.getVar("CONF_DIR"))

    write_ap_cert_list(cfg, conf_dir / "aps.list")

    # OVH API credentials + the router's SSH push key: root-only.
    install_file(
        conf_dir / "ovh.env",
        read_secret("SECRETS_DIR", "ovh.env", ROUTER_HINT),
        0o600,
    )
    install_file(
        conf_dir / "ap-push-key",
        read_secret("SECRETS_DIR", "ssh/ap-push-key", ROUTER_HINT),
        0o600,
    )

    cert_aps = cfg.cert_aps()
    if not cert_aps:
        bb.warn("futro-ap-certs: no [aps.<label>] table sets cert = true")

    # One CSR per certificate-holding AP, keyed by hostname to match
    # aps.list. The matching private key never leaves the AP — see
    # aps/gen-ap-tls.py.
    for a in cert_aps:
        install_file(
            conf_dir / "csr" / f"{a.hostname}.csr",
            read_secret("AP_SECRETS_DIR", f"tls/{a.label}/csr.pem", AP_HINT),
            0o644,
        )

    # Pin every AP's SSH host key so the push can use
    # StrictHostKeyChecking=yes. The whole fleet shares one baked dropbear
    # host key pair (apbuild installs the same secrets on every AP), so
    # each address gets the same key material listed against it.
    pubkeys = []
    for name in ("dropbear_ed25519_host_key.pub", "dropbear_rsa_host_key.pub"):
        # known_hosts wants `<patterns> <keytype> <base64>` — drop any
        # trailing comment field that dropbearkey emitted.
        fields = read_secret("AP_SECRETS_DIR", f"ssh/{name}", AP_HINT).split()
        if len(fields) < 2:
            bb.fatal("Malformed AP host public key in aps/secrets/ssh/%s" % name)
        pubkeys.append((fields[0], fields[1]))

    # This is the *system-wide* client database (/etc/ssh/ssh_known_hosts),
    # which is openssh's default GlobalKnownHostsFile — so no ssh_config
    # change is needed, and interactive `ssh ax59u` from the router gets
    # host-key verification for free rather than a TOFU prompt.
    #
    # Each entry lists every name the AP answers to (bare name, FQDN in the
    # search domain, aliases, certificate FQDN, and the address), because a
    # known_hosts lookup keys on whatever string was typed.
    # Every AP is pinned, not just the certificate-holding ones: host-key
    # verification is worth having on any AP we SSH into.
    domain = cfg.router.dns_search_domain
    lines = [
        "# Generated by futro-ap-certs from config.toml + aps/secrets — do not edit.",
        "# The AP fleet shares one baked dropbear host key pair (apbuild bakes",
        "# the same secrets into every AP), so each host below is pinned to the",
        "# same key material.",
    ]
    for a in cfg.aps:
        patterns = [a.hostname, f"{a.hostname}.{domain}", a.label, *a.aliases, str(a.ipv4)]
        if a.cert:
            patterns.append(a.cert_fqdn(cfg.acme))
        # Preserve order while dropping any duplicate (e.g. label == alias).
        seen = set()
        uniq = [p for p in patterns if not (p in seen or seen.add(p))]
        for keytype, blob in pubkeys:
            lines.append(f"{','.join(uniq)} {keytype} {blob}")

    install_file(
        pathlib.Path(d.getVar("D") + d.getVar("sysconfdir")) / "ssh" / "ssh_known_hosts",
        "\n".join(lines) + "\n",
        0o644,
    )
}
do_install[postfuncs] += "do_install_ap_certs"

# Re-run do_install whenever any generated input changes. Without this,
# bitbake's task signature only covers the SRC_URI files and silently
# skips regeneration.
do_install[file-checksums] += " \
    ${CONFIG_TOML}:True \
    ${ROUTERBUILD_ROOT}/routerbuild/config.py:True \
    ${ROUTERBUILD_ROOT}/routerbuild/render.py:True \
    ${SECRETS_DIR}/ovh.env:True \
    ${SECRETS_DIR}/ssh/ap-push-key:True \
    ${AP_SECRETS_DIR}/ssh/dropbear_ed25519_host_key.pub:True \
    ${AP_SECRETS_DIR}/ssh/dropbear_rsa_host_key.pub:True \
"

# ACME contact address, read from config.toml at parse time so the unit
# and the declarative config cannot drift.
def acme_email_from_config(d):
    import pathlib, sys
    sys.path.insert(0, d.getVar("ROUTERBUILD_ROOT"))
    from routerbuild.config import NetworkConfig
    cfg = NetworkConfig.load(pathlib.Path(d.getVar("CONFIG_TOML")))
    if cfg.acme is None:
        bb.fatal("config.toml: [acme] must be set to build futro-ap-certs")
    return cfg.acme.email

ACME_EMAIL := "${@acme_email_from_config(d)}"

FILES:${PN} = " \
    ${libexecdir}/futro-ap-certs.sh \
    ${systemd_system_unitdir}/futro-ap-certs.service \
    ${systemd_system_unitdir}/futro-ap-certs.timer \
    ${CONF_DIR} \
    ${sysconfdir}/ssh/ssh_known_hosts \
    ${localstatedir}/lib/lego \
"
