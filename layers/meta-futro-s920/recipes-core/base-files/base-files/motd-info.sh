# /etc/profile.d/motd-info.sh — system status banner for interactive logins
# Sourced by /etc/profile; safe under sh/dash/bash.

# Only on interactive shells, and only once per shell tree.
case $- in *i*) ;; *) return 0 2>/dev/null || exit 0 ;; esac
[ -n "$_MOTD_INFO_SHOWN" ] && return 0
_MOTD_INFO_SHOWN=1

# ANSI helpers: dim labels, bold values; honour NO_COLOR.
if [ -t 1 ] && [ -z "$NO_COLOR" ]; then
    _d=$(printf '\033[2m'); _b=$(printf '\033[1m'); _r=$(printf '\033[0m')
else
    _d=''; _b=''; _r=''
fi

_label() { printf '  %s%-9s%s ' "$_d" "$1" "$_r"; }

# Uptime as Dd Hh Mm
_up_secs=$(cut -d. -f1 /proc/uptime 2>/dev/null)
if [ -n "$_up_secs" ]; then
    _up_d=$(( _up_secs / 86400 ))
    _up_h=$(( (_up_secs % 86400) / 3600 ))
    _up_m=$(( (_up_secs % 3600) / 60 ))
    _up_str="${_up_d}d ${_up_h}h ${_up_m}m"
fi

printf '\n'
_label host;   printf '%s%s%s   %s\n' "$_b" "$(hostname)" "$_r" "$(uname -srm)"
[ -n "$_up_str" ] && { _label uptime; printf '%s\n' "$_up_str"; }
_label load;   printf '%s\n' "$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"

# IPv4 addresses on global-scope interfaces
if command -v ip >/dev/null 2>&1; then
    ip -o -4 addr show scope global 2>/dev/null | while read -r _ _iface _ _addr _; do
        _label "$_iface"; printf '%s\n' "$_addr"
    done
fi

# RAUC booted slot (if RAUC is installed)
if command -v rauc >/dev/null 2>&1; then
    _slot=$(rauc status --output-format=shell 2>/dev/null \
        | sed -n "s/^RAUC_SYSTEM_BOOTED_BOOTNAME='\\([^']*\\)'.*/\\1/p")
    [ -n "$_slot" ] && { _label slot; printf '%s\n' "$_slot"; }
fi

# /data usage (router persistent partition)
if [ -d /data ]; then
    _data=$(df -h /data 2>/dev/null | awk 'NR==2 { printf "%s used of %s (%s)", $3, $2, $5 }')
    [ -n "$_data" ] && { _label data; printf '%s\n' "$_data"; }
fi

printf '\n'
unset _d _b _r _up_secs _up_d _up_h _up_m _up_str _slot _data
unset -f _label 2>/dev/null
