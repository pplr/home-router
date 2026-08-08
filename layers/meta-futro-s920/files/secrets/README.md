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
| `ovh.env` | `futro-ap-certs` | OVH API credentials for lego's DNS-01 challenge |
| `ssh/ap-push-key` | `futro-ap-certs` | Private key used to push renewed certs to the APs |

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

# 4. Certificate-push key. The router uses this to deliver renewed LuCI
#    certificates to each AP. The public half must be copied into the AP
#    secrets tree, where apbuild installs it behind a forced command.
ssh-keygen -t ed25519 -N '' -C 'futro-ap-certs push key' \
    -f "$SECRETS/ssh/ap-push-key"
cp "$SECRETS/ssh/ap-push-key.pub" aps/secrets/ssh/ap-push-key.pub

# 5. OVH API credentials for the DNS-01 challenge (see below for how to
#    create them). Endpoint is ovh-eu for .eu domains on OVH Europe.
cat > "$SECRETS/ovh.env" <<'EOF'
OVH_ENDPOINT=ovh-eu
OVH_APPLICATION_KEY=CHANGE_ME
OVH_APPLICATION_SECRET=CHANGE_ME
OVH_CONSUMER_KEY=CHANGE_ME
EOF
chmod 600 "$SECRETS/ovh.env" "$SECRETS/ssh/ap-push-key"
```

## OVH API credentials

`lego` proves control of the zone by creating and deleting
`_acme-challenge.<name>` TXT records, so it needs an OVH API application
whose consumer key is authorised for **just that zone**.

1. Create an application at <https://eu.api.ovh.com/createApp/> — this
   yields `OVH_APPLICATION_KEY` and `OVH_APPLICATION_SECRET`.
2. Request a consumer key scoped to the zone only, not your whole
   account. Using <https://eu.api.ovh.com/createToken/>, grant exactly:

   | Method | Path |
   |---|---|
   | `GET` | `/domain/zone/lplr.eu/*` |
   | `POST` | `/domain/zone/lplr.eu/*` |
   | `DELETE` | `/domain/zone/lplr.eu/*` |

   That is the minimum lego needs: list/create/delete TXT records and
   refresh the zone. Do **not** grant `/*` — a leaked key would then
   reach billing, DNS for every domain, and server management.
3. Set `validity` to unlimited (or diarise a rotation), since the router
   renews unattended.

Only the ephemeral `_acme-challenge` TXT records ever reach the public
zone. The AP `A` records are local-only, served from the router's
generated `/etc/hosts` — see `[acme].domain` in the repo-root
`config.toml`.

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
- The OVH API credentials can create and delete DNS records in the zone;
  a leak would let an attacker issue certificates for it.
- The AP push key is restricted to a forced command on the AP side, so it
  cannot open a shell — but it is still an authorised credential into
  every AP, so it gets the same treatment as the host keys.

The committed RAUC dev signing key under `files/rauc-keys/` is a known
exception: it's a development CA explicitly meant for local builds. Real
production deployments should keep that out-of-tree too.
