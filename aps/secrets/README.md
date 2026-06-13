# Out-of-tree AP secrets

Files in this directory (except this README) are **gitignored**. They
are read at build time by `aps/build.py` and baked into the OpenWrt
sysupgrade image — same out-of-tree-secrets discipline as the router's
`layers/meta-futro-s920/files/secrets/`.

| File | Consumer | Purpose |
|---|---|---|
| `root.hash` | `apbuild.render` (`/etc/shadow` rewrite) | SHA-512 crypt hash for `root` on both APs |
| `ssh/authorized_keys` | `apbuild.render` → `/etc/dropbear/authorized_keys` | Public keys allowed to SSH in as root |
| `ssh/dropbear_ed25519_host_key` | `apbuild.render` → `/etc/dropbear/` | Baked ed25519 host key (binary, dropbear format) |
| `ssh/dropbear_rsa_host_key` | `apbuild.render` → `/etc/dropbear/` | Baked RSA host key (binary, dropbear format) |
| `wifi/psk-trusted` | `apbuild.render` (via `[common.ssids.trusted].psk_secret` in `aps/config.toml`) | PSK for `maison-jaune` (SAE/WPA3) |
| `wifi/psk-iot` | `apbuild.render` (via `[common.ssids.iot].psk_secret` in `aps/config.toml`) | PSK for `maison-jaune-iot` (WPA2-PSK) |
| `netdata/stream_api_key` | `apbuild.render` → `/etc/netdata/stream.conf` | netdata streaming API key (UUID). **Only required for APs with `netdata = true`.** Must be identical to the router's `layers/meta-futro-s920/files/secrets/netdata/stream_api_key`. |

The set of PSK files is **data-driven**: it follows from the `psk_secret` paths declared in `aps/config.toml`'s `[common.ssids.*]` tables. Add an SSID there, and a new PSK file becomes required here.

If any of these is missing or empty, `aps/build.py` fails fast with a
`MissingSecretError` naming the exact path.

## Generating the secrets

Run from the repo root. None of the commands below talk to the network.

```bash
SECRETS=aps/secrets
mkdir -p "$SECRETS/ssh" "$SECRETS/wifi"

# 1. Root password hash (shared by both APs).
#    Use a long, random passphrase (20+ chars or 6+ diceware words).
#    rounds=500000 hardens against offline cracking at negligible login cost.
mkpasswd -m sha512crypt -R 500000 > "$SECRETS/root.hash"

# 2. Authorized SSH key(s). Paste one or more `ssh-ed25519 …` /
#    `ssh-rsa …` lines — typically `~/.ssh/id_ed25519.pub` from the
#    workstation you'll manage the APs from.
cat ~/.ssh/id_ed25519.pub > "$SECRETS/ssh/authorized_keys"

# 3. Dropbear host keys. Both types are baked so dropbear's first-boot
#    key-regen path stays a no-op, keeping the AP's SSH fingerprint
#    stable across sysupgrade -n. The files are dropbear's *native*
#    binary format, NOT OpenSSH PEM — generate with `dropbearkey`.
#
#    If you don't have dropbearkey locally, build it from OpenWrt
#    source or run it on an existing AP and scp the output back.
dropbearkey -t ed25519 -f "$SECRETS/ssh/dropbear_ed25519_host_key"
dropbearkey -t rsa     -f "$SECRETS/ssh/dropbear_rsa_host_key" -s 4096

# 4. Wi-Fi PSKs. WPA3-SAE accepts any string (no length cap) but the
#    underlying 4-way handshake still benefits from high entropy —
#    aim for 20+ chars / 6+ diceware words for both.
#    Newlines are stripped at load time, so a trailing newline from
#    your editor is fine; no internal whitespace, please.
printf 'CHANGE_ME_TRUSTED_PSK\n' > "$SECRETS/wifi/psk-trusted"
printf 'CHANGE_ME_IOT_PSK\n'     > "$SECRETS/wifi/psk-iot"
chmod 600 "$SECRETS/root.hash" "$SECRETS/ssh/dropbear_"* "$SECRETS/wifi/"*

# 5. netdata streaming API key (only for APs with `netdata = true`).
#    A UUID shared with the router's netdata parent — generate ONCE and
#    copy the SAME value to the router's secrets dir (see CLAUDE.md).
mkdir -p "$SECRETS/netdata"
uuidgen > "$SECRETS/netdata/stream_api_key"     # or: python3 -c 'import uuid;print(uuid.uuid4())'
chmod 600 "$SECRETS/netdata/stream_api_key"
```

## Rotating

To rotate any secret: regenerate the file(s) with the same commands
above, then `./aps/build.py <ap>` and `sysupgrade -n` the new image
onto each AP. Secrets are baked into the squashfs (read-only), so each
new image ships the current value.

## Why these are out-of-tree (gitignored)

- The root password hash is offline-crackable if you ever publish the
  repo. Keeping it out of git history removes that risk.
- Dropbear host **private** keys must never be committed.
- Wi-Fi PSKs grant network access to the trusted LAN; same trust
  boundary as the router's `pplr.hash`.
- `authorized_keys` is technically not secret (it's public-key
  material) but kept here for cohesion with the rest of the bundle.

## Format gotchas

- `root.hash` should contain a single line like
  `$6$rounds=500000$<salt>$<hash>`. Trailing newlines are stripped at
  load time; the dollar signs are passed through verbatim — there is
  no shell in the rendering path, so the router's "useradd `$`-eating
  trap" doesn't apply here.
- Dropbear's `dropbearkey` writes a small binary blob, NOT a PEM. If
  your file starts with `-----BEGIN OPENSSH PRIVATE KEY-----`, it's
  the wrong format and `dropbear` will reject it on boot.
- `psk-trusted` / `psk-iot` files should contain just the PSK, one
  line. Don't quote, don't add comments.
