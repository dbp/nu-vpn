# nu-vpn

Hands-off sign-in for the Northeastern GlobalProtect VPN (openconnect via
NetworkManager, on KDE Plasma).

Click **Connect** on *Northeastern VPN* in Plasma's network applet. A few
seconds later a notification shows **Duo code: 482**. Enter that number in
the Duo push on your phone and the VPN comes up. Nothing to type or click on
the computer. When Microsoft's "stay signed in" and Duo's "remember this
device" are still valid, there's no Duo step at all.

Prefer typing a Duo Mobile passcode into a box on the computer instead? Set
`duo = passcode` in the config.

## Setup

```sh
./install.sh          # command in ~/.local/bin, systemd user service, config
nu-vpn setup          # username -> ~/.config/nu-vpn/config.ini, password -> keyring
```

## How it works

NetworkManager gets VPN cookies from *secret agents*, and it asks the one
started most recently first. `nu-vpn agent` (the `nu-vpn-agent` user service)
starts after Plasma's kded, so it answers for the configured connection:

1. It asks the portal (`/global-protect/prelogin.esp`) for the SAML request.
2. A hidden WebKit view runs the Microsoft sign-in. It fills in the username
   and the password from the keyring and clicks *Continue* on "Verification
   Required" (the hand-off to Duo). On Duo (`duo = push`) it waits for the
   push Duo sends by itself. If Duo opens on another method, it switches to
   *Duo Push*. If there's no push or it times out, it presses send/try again,
   up to three pushes. It reads the verified-push number off the page and
   shows it in a notification that stays until the sign-in is done. With
   `duo = passcode` it picks *Other options → Duo Mobile passcode* and asks
   you for the code instead. Then it clicks *Yes, this is my device* and
   *Stay signed in*. Cookies persist in `~/.local/share/nu-vpn/`.
   Microsoft's pages re-render about 0.8 s after they appear, swapping in
   fresh elements, so it waits for that before filling or clicking anything.
3. It captures `prelogin-cookie` and `saml-username` from the portal's ACS
   response, then runs `openconnect --authenticate
   --usergroup=portal:prelogin-cookie` to get the gateway cookie.
4. It hands `cookie`/`gateway`/`gwcert`/`resolve` to NetworkManager, which
   connects as usual. Disconnect works from the applet.

Other connections, and anything other than Plasma's Connect, go to the
remaining agents unchanged.

What happens when things go wrong:

- **Deny** on the Duo push, or **Cancel** on the code box, cancels the
  connection. So does a push nobody approves (three tries).
- **Microsoft rejects the saved password**: you're asked for the new one once,
  and it's saved.
- **A page it doesn't recognise** (say a new Microsoft interstitial): after
  20 s the browser window appears so you can get past that page by hand. As
  soon as the next page is one it knows, the window hides again and it
  carries on.
- **Any other failure** (openconnect error, timeout): you get a notification,
  and the next round falls through to Plasma's old dialog.

## Commands

| command | what it does |
|---|---|
| `nu-vpn setup` | save username and password (`--keep-password` to change only the username) |
| `nu-vpn connect` | same as clicking Connect (don't use `nmcli connection up`: nmcli cancels the secrets request itself) |
| `nu-vpn login` | run the sign-in once without connecting; prints the gateway |
| `nu-vpn login --probe` | load the sign-in and describe the first step it recognises, submitting nothing |
| `nu-vpn forget` | clear the remembered Microsoft/Duo session |

## Config: `~/.config/nu-vpn/config.ini`

```ini
[vpn]
uuid = c8e5ef8b-...          # NetworkManager connection(s), space separated
gateway = pagw-10.neu.edu    # openconnect --authgroup; blank = portal's default

[login]
username = you@northeastern.edu
duo = push                   # or: passcode
```

Portal host, user agent and reported OS come from the NetworkManager
connection, so the cookie matches what NetworkManager connects with.

## Troubleshooting

```sh
journalctl --user -u nu-vpn-agent -e     # what the agent and sign-in did
cat ~/.cache/nu-vpn/last-login.log       # page-by-page trace of the last sign-in
nu-vpn login                             # run it in the foreground
```

The trace records page hosts and paths, the step it recognised, error text
and button labels. It never records field values.

`tests/mock_flow.py` runs the state machine against local mock pages
(Microsoft, Duo, the portal ACS) with faked kdialog and keyring:

```sh
python3 tests/mock_flow.py
```

## Uninstall

```sh
systemctl --user disable --now nu-vpn-agent.service
rm ~/.config/systemd/user/nu-vpn-agent.service ~/.local/bin/nu-vpn
rm -r ~/.config/nu-vpn ~/.local/share/nu-vpn ~/.cache/nu-vpn
# then delete "Northeastern VPN password (...)" from the Login keyring (Seahorse),
# or: secret-tool clear username you@northeastern.edu   (package libsecret-tools)
```

Requires: python3-gi, gir1.2-webkit-6.0, gir1.2-gtk-4.0, gir1.2-secret-1,
python3-dbus, kdialog, openconnect, network-manager-openconnect.
