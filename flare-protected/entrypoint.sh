#!/bin/sh
set -eu

fail_closed() {
    echo "protected FlareSolverr network policy setup failed" >&2
    exit 1
}

command -v iptables >/dev/null 2>&1 || fail_closed
command -v ip6tables >/dev/null 2>&1 || fail_closed
command -v setpriv >/dev/null 2>&1 || fail_closed

for tool in iptables ip6tables; do
    "$tool" -w -F INPUT || fail_closed
    "$tool" -w -F OUTPUT || fail_closed
    "$tool" -w -F FORWARD || fail_closed
    "$tool" -w -P INPUT DROP || fail_closed
    "$tool" -w -P OUTPUT DROP || fail_closed
    "$tool" -w -P FORWARD DROP || fail_closed
    "$tool" -w -A INPUT -i lo -j ACCEPT || fail_closed
    "$tool" -w -A OUTPUT -o lo -j ACCEPT || fail_closed
    "$tool" -w -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT || fail_closed
    "$tool" -w -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT || fail_closed
done

# The capture network assigns this fixed address to the guarded gateway.
iptables -w -A OUTPUT -p tcp -d 172.31.254.18/32 --dport 8080 -j ACCEPT || fail_closed

flare_uid=$(id -u flaresolverr) || fail_closed
flare_gid=$(id -g flaresolverr) || fail_closed
control_gid=$(getent group flarecontrol | cut -d: -f3) || fail_closed
socket_dir=/run/flaresolverr
mkdir -p "$socket_dir" || fail_closed
chown "$flare_uid:$control_gid" "$socket_dir" || fail_closed

exec setpriv \
    --reuid="$flare_uid" \
    --regid="$flare_gid" \
    --init-groups \
    --bounding-set=-all \
    --no-new-privs \
    /bin/sh -c '
        chmod 2770 /run/flaresolverr || exit 1
        HOME=/tmp/groktocrawl-flare-home
        XDG_CACHE_HOME=/tmp/groktocrawl-flare-home/.cache
        XDG_DATA_HOME=/tmp/groktocrawl-flare-home/.local/share
        XDG_CONFIG_HOME=/tmp/groktocrawl-flare-home/.config
        export HOME XDG_CACHE_HOME XDG_DATA_HOME XDG_CONFIG_HOME
        umask 077
        mkdir -p "$HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" || exit 1
        chmod 0700 "$HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" || exit 1
        exec "$@"
    ' protected-flare "$@"
