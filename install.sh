#!/bin/sh
# Install nu-vpn for the current user: command, config, systemd user service.
set -eu
here=$(cd "$(dirname "$0")" && pwd)

mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user" "$HOME/.config/nu-vpn"
ln -sf "$here/nu-vpn" "$HOME/.local/bin/nu-vpn"
install -m 644 "$here/nu-vpn-agent.service" "$HOME/.config/systemd/user/nu-vpn-agent.service"

cfg="$HOME/.config/nu-vpn/config.ini"
if [ ! -e "$cfg" ]; then
    # The first GlobalProtect openconnect connection in NetworkManager.
    uuid=$(nmcli -t -f UUID,TYPE connection show | awk -F: '$2=="vpn"{print $1}' | while read -r u; do
        if nmcli -g vpn.data connection show "$u" | grep -q 'protocol = gp'; then echo "$u"; break; fi
    done)
    [ -n "$uuid" ] || { echo "no GlobalProtect VPN connection found in NetworkManager" >&2; exit 1; }
    cat > "$cfg" <<EOF
[vpn]
# NetworkManager connection(s) nu-vpn signs in for
uuid = $uuid
# GlobalProtect gateway to pick from the portal's list (optional)
gateway =

[login]
# As typed on the Microsoft sign-in page. The password lives in the keyring.
username =
EOF
    echo "wrote $cfg"
fi

systemctl --user daemon-reload
systemctl --user enable --now nu-vpn-agent.service
systemctl --user restart nu-vpn-agent.service
echo "nu-vpn agent running. If you haven't yet, save your password with:  nu-vpn setup"
