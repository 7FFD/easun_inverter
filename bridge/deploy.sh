#!/bin/sh
# Usage: bridge/deploy.sh root@<router>      (build first: bridge/build.sh)
#
# Everything goes over ONE ssh connection as a tar stream, so you enter the password once.
# (Plain ssh, no scp/sftp: OpenWrt's dropbear has no sftp-server.)
set -e
R="${1:?usage: deploy.sh root@router}"
D="$(cd "$(dirname "$0")" && pwd)"
BIN=out/easun-bridge-mips
[ -f "$D/$BIN" ] || { echo "$D/$BIN not found - run bridge/build.sh first" >&2; exit 1; }

COPYFILE_DISABLE=1 tar cf - -C "$D" "$BIN" easun-bridge.init easun-bridge.conf.example |
	ssh "$R" '
set -e
T=$(mktemp -d /tmp/easun-deploy.XXXXXX)
trap "rm -rf $T" EXIT
tar xf - -C "$T"
# write next to the target, then rename: a failed copy never leaves a half-written binary
cp "$T/out/easun-bridge-mips" /usr/bin/easun-bridge.new
chmod +x /usr/bin/easun-bridge.new
mv /usr/bin/easun-bridge.new /usr/bin/easun-bridge
cp "$T/easun-bridge.init" /etc/init.d/easun-bridge
chmod +x /etc/init.d/easun-bridge
cp "$T/easun-bridge.conf.example" /etc/easun-bridge.conf.example
ls -la /usr/bin/easun-bridge
if [ -f /etc/easun-bridge.conf ]; then
	/etc/init.d/easun-bridge restart && echo "service restarted"
else
	echo "NOTE: create /etc/easun-bridge.conf (copy /etc/easun-bridge.conf.example), then:"
	echo "      /etc/init.d/easun-bridge enable && /etc/init.d/easun-bridge start"
fi
'
