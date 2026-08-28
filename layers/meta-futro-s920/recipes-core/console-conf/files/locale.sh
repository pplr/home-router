# /etc/locale.conf only reaches systemd units: sshd builds a fresh environment
# for user sessions (do_setup_env() forwards TZ and nothing else), and OE ships
# no AcceptEnv either, so a login shell would otherwise get no LANG at all.
export LANG=C.UTF-8
