# Out-of-tree secrets

Files in this directory (except this README) are **gitignored**. They are read
at recipe parse / `do_install` time to bake static identity into the image:

| File | Consumer | Purpose |
|---|---|---|
| `pplr.hash` | `core-image-minimal.bbappend` (extrausers) | SHA-512 crypt hash for user `pplr` |
| `machine-id` | `base-files_%.bbappend` | Static `/etc/machine-id` (32 hex chars) |
| `ssh/ssh_host_ed25519_key{,.pub}` | `openssh_%.bbappend` | Baked-in ed25519 host key |
| `ssh/ssh_host_rsa_key{,.pub}` | `openssh_%.bbappend` | Baked-in RSA host key |
| `ssh/ssh_host_ecdsa_key{,.pub}` | `openssh_%.bbappend` | Baked-in ECDSA host key |

If any of these is missing, the build fails fast with a clear `bb.fatal` message.

## Generating the secrets

Run from the repo root:

```bash
SECRETS=layers/meta-futro-s920/files/secrets

# 1. Password hash for user `pplr`.
#    Use a long, random passphrase (20+ chars or 6+ diceware words).
#    rounds=500000 hardens against offline cracking at negligible login cost.
mkpasswd -m sha512crypt -R 500000 > "$SECRETS/pplr.hash"

# 2. machine-id (32 lowercase hex chars + trailing newline).
#    python3's uuid.uuid4().hex gives the exact format systemd wants;
#    avoids portability issues between GNU tr and uutils `tr`.
python3 -c "import uuid; print(uuid.uuid4().hex)" > "$SECRETS/machine-id"

# 3. SSH host keys (ed25519 + rsa + ecdsa). Comment is informational only.
#    All three are baked so sshdgenkeys.service stays a no-op — otherwise the
#    un-baked key types are regenerated on every fresh RAUC slot and the
#    device's SSH fingerprint flips when the client happens to negotiate one.
mkdir -p "$SECRETS/ssh"
ssh-keygen -t ed25519 -N '' -f "$SECRETS/ssh/ssh_host_ed25519_key" -C home-router
ssh-keygen -t rsa -b 4096 -N '' -f "$SECRETS/ssh/ssh_host_rsa_key" -C home-router
ssh-keygen -t ecdsa -b 256 -N '' -f "$SECRETS/ssh/ssh_host_ecdsa_key" -C home-router
```

> Note: AP metrics use no shared secret. The router's VictoriaMetrics scrapes
> each AP's `prometheus-node-exporter-lua` endpoint (`http://<ap>:9100/metrics`)
> over the trusted LAN. Any old `netdata/stream_api_key` file here is unused and
> can be deleted.

## Rotating

To rotate any secret: regenerate the file(s) with the same commands above,
`bitbake core-image-minimal && bitbake futro-s920-bundle`, and deploy via
RAUC. The secrets are **not** stored on `/data`; they live in the rootfs
slots, so each new bundle ships the current value.

## Why these are out-of-tree (gitignored)

- The password hash, even at sha512crypt rounds=500000, is offline-crackable
  if you ever publish the repo. Keeping it out of git history removes that
  risk.
- SSH host **private** keys must never be committed. Treating them like the
  password keeps the same trust boundary.
- The machine-id is "confidential" per systemd's documentation — same
  treatment for consistency.

The committed RAUC dev signing key under `files/rauc-keys/` is a known
exception: it's a development CA explicitly meant for local builds. Real
production deployments should keep that out-of-tree too.
