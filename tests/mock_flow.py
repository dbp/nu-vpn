#!/usr/bin/python3
"""Run nu-vpn's sign-in state machine against local mock pages.

The mocks imitate the bits of Microsoft's sign-in, Duo's Universal Prompt and
the GlobalProtect ACS response that nu-vpn relies on (element ids, an
off-screen decoy password field, React-style inputs that only see `input`
events). Nothing leaves the machine; kdialog and the keyring are faked.

    python3 tests/mock_flow.py
"""

import http.server
import importlib.machinery
import importlib.util
import json
import socketserver
import sys
import tempfile
import threading
import time
import types
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
USER = "testuser@example.edu"
GOOD_PW, GOOD_CODE = "hunter2", "123456"
EVENTS = []
OPTIONS = {}
TRACE = []


def serve(handler):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


class Base(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, status=200, headers=()):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))


MS_LOGIN = """<!doctype html><html><head><title>Sign in to your account</title></head><body>
<input type="password" name="passwd" class="moveOffScreen" tabindex="-1" aria-hidden="true"
       style="position:absolute;left:-10px;top:-13px;width:10px;height:13px">
<form id="f" method="post" action="/login">
  <div id="userPane">
    <div id="loginHeader" role="heading">Sign in</div>
    <div id="usernameError" style="display:none">We couldn't find an account with that username.</div>
    <input type="email" name="loginfmt" id="i0116" placeholder="Email or Username">
  </div>
  <div id="pwPane" style="display:none">
    <div role="heading">Enter password</div>
    <div id="passwordError" style="display:__PWERR__">Your account or password is incorrect.</div>
    <input type="password" name="passwd" id="i0118">
  </div>
  <input type="submit" id="idSIButton9" value="Next">
</form>
<script>
  // Like Knockout's textInput binding: the model only changes on input events.
  let pane = '__PANE__', typedUser = '__USER__', typedPw = '';
  const show = () => { userPane.style.display = pane === 'user' ? '' : 'none';
                       pwPane.style.display = pane === 'pw' ? '' : 'none';
                       idSIButton9.value = pane === 'user' ? 'Next' : 'Sign in'; };
  const bindUser = el => el.addEventListener('input', e => typedUser = e.target.value);
  bindUser(i0116);
  i0118.addEventListener('input', e => typedPw = e.target.value);
  // Like the real page: ~0.7 s in, the username box is swapped for a fresh,
  // empty one (and the form posts whatever that box holds).
  if (pane === 'user') setTimeout(() => {
    const fresh = document.createElement('input');
    fresh.type = 'email'; fresh.name = 'loginfmt'; fresh.id = 'i0116';
    document.getElementById('i0116').replaceWith(fresh);
    bindUser(fresh);
  }, 700);
  // A page that opens on the password (Microsoft remembers you) swaps that box too.
  if (pane === 'pw') setTimeout(() => {
    const fresh = document.createElement('input');
    fresh.type = 'password'; fresh.name = 'passwd'; fresh.id = 'i0118';
    document.getElementById('i0118').replaceWith(fresh);
    fresh.addEventListener('input', e => typedPw = e.target.value);
  }, 700);
  idSIButton9.addEventListener('click', ev => {
    if (pane === 'user') {
      ev.preventDefault();
      if (!typedUser.includes('@')) { usernameError.style.display = ''; return; }
      setTimeout(() => { pane = 'pw'; show(); }, 300);      // credential-type lookup
    } else if (document.getElementById('i0118').value !== typedPw) {
      ev.preventDefault();                                  // didn't see an input event
    }                                                       // else: native form post
  });
  show();
</script></body></html>"""

MS_ERROR = """<!doctype html><html><head><title>Sign in to your account</title></head><body>
<div>Sorry, but we're having trouble signing you in.</div>
<div>AADSTS90100: login parameter is empty or not valid.</div>
<div role="heading">Troubleshooting details</div><button>Copy info to clipboard</button>
</body></html>"""

# Every Microsoft page re-renders ~0.7 s in; the buttons are swapped for fresh
# ones and clicks on the old ones go nowhere.
MS_SWAP = """<script>
  function swapLater(id, onclick) {
    document.getElementById(id).onclick = () => {};
    setTimeout(() => {
      const old = document.getElementById(id), fresh = old.cloneNode(true);
      fresh.onclick = onclick; old.replaceWith(fresh);
    }, 700);
  }
</script>"""

MS_KMSI = """<!doctype html><html><head><title>Sign in to your account</title></head><body>
<div role="heading">Stay signed in?</div>
<label><input type="checkbox" id="KmsiCheckboxField"> Don't show this again</label>
<input type="button" id="idBtn_Back" value="No"> <input type="button" id="idSIButton9" value="Yes">
<form id="saml" method="post" action="__ACS__"><input type="hidden" name="SAMLResponse" value="mock"></form>
""" + MS_SWAP + """<script>
  swapLater('idSIButton9', () => { saml.action += '?kmsi=' + (KmsiCheckboxField.checked ? 1 : 0); saml.submit(); });
</script></body></html>"""

MS_NOTICE = """<!doctype html><html><head><title>Sign in to your account</title></head><body>
<div role="heading">Something new from Microsoft</div>
<button id="gotit" onclick="location = '/verify'">Got it</button></body></html>"""

MS_VERIFY = """<!doctype html><html><head><title>Sign in to your account</title></head><body>
<div role="heading">Verification Required</div>
<p>Your organization requires additional verification.</p>
<input type="button" id="idBtn_Back" value="Back"> <input type="submit" id="idSIButton9" value="Continue">
""" + MS_SWAP + """<script>
  swapLater('idSIButton9', () => { fetch('/continued', {method: 'POST'}).then(() => location = '__DUO__'); });
</script></body></html>"""

DUO = """<!doctype html><html><head><title>Duo Security</title></head><body><div id="app"></div>
<script>
  // Universal Prompt-ish: opens on the last method used; push is sent automatically.
  let view = '__START__', err = false, code = '', pushN = 0, vcode = '';
  const VERIFIED = __VERIFIED__;
  const app = document.getElementById('app');
  const other = '<a href="#" class="button--link other-options-link">Other options</a>';
  function startPush() {
    view = 'push';
    fetch('/push', {method: 'POST'}).then(r => r.json()).then(p => { pushN = p.n; vcode = p.code; render(); poll(); });
  }
  function poll() {
    fetch('/status?n=' + pushN).then(r => r.text()).then(st => {
      if (view !== 'push') return;
      if (st === 'pending') return setTimeout(poll, 300);
      view = st === 'approve' ? 'trust' : st;              // 'timeout' or 'deny'
      setTimeout(render, 200);
    });
  }
  function render() {
    if (view === 'push') app.innerHTML = '<h1>Check for a Duo Push</h1>' +
        (VERIFIED ? '<p>Enter this code in Duo Mobile</p><div class="verification-code" style="font-size:36px">' +
                    vcode.split('').map(d => '<span>' + d + '</span>').join(' ') + '</div>' : '') +
        '<p>Sent to "iPhone" (<span>1234</span>)</p>' + other;
    if (view === 'timeout') app.innerHTML = '<h1>Push timed out</h1><button id="retry">Try again</button>' + other;
    if (view === 'deny') app.innerHTML = '<h1>Request denied</h1><div role="alert">Login request denied.</div>' +
        '<button id="retry">Try again</button>' + other;
    if (view === 'methods') app.innerHTML = '<h1>Other options to log in</h1><ul>' +
        '<li data-testid="test-id-push"><a href="#" id="m-push">Duo Push <span>Recommended</span></a></li>' +
        '<li data-testid="test-id-mobile-otp"><a href="#" id="m-otp">Duo Mobile passcode</a></li></ul>';
    if (view === 'code') app.innerHTML = '<h1>Enter code from Duo Mobile</h1>' +
        (err ? '<div role="alert" class="verify-error">Incorrect passcode. Enter a new passcode.</div>' : '') +
        '<input id="passcode-input" name="passcode-input" autocomplete="one-time-code">' +
        '<button type="submit" data-testid="verify-button">Verify</button>' + other;
    if (view === 'trust') app.innerHTML = '<h1>Is this your device?</h1>' +
        '<button id="trust-browser-button">Yes, this is my device</button>' +
        '<button id="dont-trust-browser-button">No, other people use this device</button>';
    const on = (sel, f) => { const e = app.querySelector(sel); if (e) e.addEventListener('click', ev => { ev.preventDefault(); f(); }); };
    on('.other-options-link', () => { view = 'methods'; setTimeout(render, 200); });
    on('#m-otp', () => { view = 'code'; setTimeout(render, 200); });
    on('#m-push', () => setTimeout(startPush, 200));
    on('#retry', () => setTimeout(startPush, 200));
    const pc = app.querySelector('#passcode-input');
    if (pc) pc.addEventListener('input', e => code = e.target.value);   // React-style state
    on('[data-testid=verify-button]', async () => {
      const r = await fetch('/check', {method: 'POST', body: code});
      if ((await r.text()) === 'ok') { view = 'trust'; err = false; } else { err = true; }
      setTimeout(render, 300);
    });
    on('#trust-browser-button', () => { fetch('/trust', {method: 'POST'}).then(() => location = '__MS__/kmsi'); });
  }
  setTimeout(() => view === 'push' ? startPush() : render(), 400);
</script></body></html>"""


def make_servers():
    ports = {}

    class MS(Base):
        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/saml2":
                self.send(MS_LOGIN.replace("__PANE__", "user").replace("__USER__", "")
                          .replace("__PWERR__", "none"))
            elif path == "/verify":
                self.send(MS_VERIFY.replace("__DUO__", f"http://localhost:{ports['duo']}/frame/v4/auth/prompt"))
            elif path == "/error":
                self.send(MS_ERROR)
            elif path == "/kmsi":
                self.send(MS_KMSI.replace("__ACS__", f"http://127.0.0.1:{ports['gp']}/SAML20/SP/ACS"))
            else:
                self.send("nope", 404)

        def do_POST(self):
            if self.path == "/continued":
                EVENTS.append(("continue",))
                self.send("ok")
                return
            f = self.form()
            EVENTS.append(("password", f.get("loginfmt"), f.get("passwd")))
            if not f.get("loginfmt"):
                self.send(MS_ERROR)
            elif f.get("passwd") == GOOD_PW and OPTIONS.get("notice"):
                self.send(MS_NOTICE)
            elif f.get("passwd") == GOOD_PW:
                self.send(MS_VERIFY.replace("__DUO__", f"http://localhost:{ports['duo']}/frame/v4/auth/prompt"))
            else:
                self.send(MS_LOGIN.replace("__PANE__", "pw").replace("__USER__", f.get("loginfmt", ""))
                          .replace("__PWERR__", "")
                          .replace('id="i0116"', f'id="i0116" value="{f.get("loginfmt", "")}"'))

    pushes = []          # start time of each push sent

    class Duo(Base):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/status":
                n = int(urllib.parse.parse_qs(url.query)["n"][0])
                outcomes = OPTIONS.get("pushes") or []
                outcome = outcomes[n - 1] if n <= len(outcomes) else "approve"
                done = time.monotonic() - pushes[n - 1] > OPTIONS.get("push_delay", 1.0) and outcome != "never"
                self.send(outcome if done else "pending")
                return
            self.send(DUO.replace("__MS__", f"http://127.0.0.1:{ports['ms']}")
                      .replace("__START__", OPTIONS.get("duo_start", "push"))
                      .replace("__VERIFIED__", "true" if OPTIONS.get("verified") else "false"))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode()
            if self.path == "/check":
                EVENTS.append(("code", body))
                self.send("ok" if body == GOOD_CODE else "bad")
            elif self.path == "/push":
                pushes.append(time.monotonic())
                EVENTS.append(("push", len(pushes)))
                self.send(json.dumps({"n": len(pushes), "code": str(400 + 11 * len(pushes))}))
            else:
                EVENTS.append(("trust",))
                self.send("ok")

    class GP(Base):
        def do_POST(self):
            EVENTS.append(("acs", urllib.parse.urlsplit(self.path).query))
            self.form()
            self.send("<html><!-- <saml-auth-status>1</saml-auth-status> --></html>", 200, [
                ("saml-auth-status", "1"), ("saml-username", USER), ("prelogin-cookie", "PRELOGIN123")])

    ports["ms"], ports["duo"], ports["gp"] = serve(MS), serve(Duo), serve(GP)
    return ports


NOTES = []


def load_nuvpn(ports, tmp, codes, typed_pw):
    loader = importlib.machinery.SourceFileLoader("nuvpn", str(REPO / "nu-vpn"))
    spec = importlib.util.spec_from_loader("nuvpn", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    # Point the host checks at the mocks.
    mod.JS_CLASSIFY = (mod.JS_CLASSIFY
                       .replace(r"/(^|\.)(login\.microsoftonline\.com|login\.microsoft\.com|login\.windows\.net)$/",
                                rf"/^127\.0\.0\.1:{ports['ms']}$/")
                       .replace(r"/(^|\.)duosecurity\.com$/", rf"/^localhost:{ports['duo']}$/"))
    assert str(ports["ms"]) in mod.JS_CLASSIFY and str(ports["duo"]) in mod.JS_CLASSIFY
    mod.DATA_DIR, mod.CACHE_DIR = Path(tmp) / "data", Path(tmp) / "cache"
    mod.TRACE_FILE = Path(tmp) / "trace.log"
    keyring = {}
    mod.get_password = lambda u: keyring.get(u)
    mod.store_password = lambda u, p: keyring.__setitem__(u, p)
    prompts = []

    def fake_kdialog(kind, text, default=""):
        prompts.append((kind, text))
        answer = typed_pw if kind == "password" else (codes.pop(0) if codes else None)
        if answer is None:                       # the user pressed Cancel
            return ["/bin/sh", "-c", "exit 1"]
        return ["/bin/sh", "-c", 'printf "%s\\n" "$1"', "sh", answer]
    mod.kdialog_argv = fake_kdialog
    NOTES.clear()

    def fake_notify(text, replaces=0, timeout_ms=10000, summary="", urgent=False):
        NOTES.append(("show", summary, text, urgent))
        return 42
    mod.notify = fake_notify
    mod.close_notification = lambda nid: NOTES.append(("close", nid))

    def no_window(self, why, st=None):       # never put a test window on screen
        if not OPTIONS.get("user_clicks"):
            self.stop(error=mod.Abort(f"would show the window: {why}", 9))
            return
        # Pretend the window is up and the user clicks the button on the page.
        self.trace(f"(test) window shown: {why}")
        self.shown = True
        self.shown_at = (st or {}).get("host"), (st or {}).get("path"), (st or {}).get("state")
        self.GLib.timeout_add(1000, lambda: (self.js(OPTIONS["user_clicks"]), False)[1])
    mod.SamlBrowser.show = no_window
    mod.STUCK_AFTER = 2
    return mod, keyring, prompts


def scenario(name, stored_pw, codes, typed_pw=GOOD_PW, interactive=True, user=USER, start="/saml2",
             duo="push", **options):
    EVENTS.clear()
    OPTIONS.clear()
    if duo == "passcode":
        OPTIONS["pushes"] = ["never"] * 5     # nobody approves the automatic push
    OPTIONS.update(options)
    ports = make_servers()
    with tempfile.TemporaryDirectory() as tmp:
        mod, keyring, prompts = load_nuvpn(ports, tmp, list(codes), typed_pw)
        if stored_pw:
            keyring[user] = stored_pw
        b = mod.SamlBrowser(types.SimpleNamespace(host="127.0.0.1"), user, interactive,
                            time.monotonic() + 60, duo=duo)
        t0 = time.monotonic()
        try:
            result = b.run("REDIRECT", f"http://127.0.0.1:{ports['ms']}{start}")
            outcome = ("ok", result)
        except mod.Abort as e:
            outcome = ("abort", e.code, str(e))
        took = time.monotonic() - t0
        TRACE[:] = b.trace_lines
    print(f"\n== {name}  ({took:.1f}s)")
    print("   outcome:", outcome)
    print("   events: ", EVENTS)
    print("   prompts:", [k for k, _ in prompts])
    print("   notes:  ", NOTES)
    return outcome, list(EVENTS), prompts


def main():
    failures = []

    def check(cond, what):
        print(("   PASS " if cond else "   FAIL ") + what)
        if not cond:
            failures.append(what)

    out, ev, pr = scenario("happy path: saved password, push screen -> passcode, one wrong code",
                           GOOD_PW, ["000000", GOOD_CODE], duo="passcode")
    check(out[0] == "ok" and out[1].get("prelogin-cookie") == "PRELOGIN123"
          and out[1].get("saml-username") == USER, "got prelogin-cookie and saml-username")
    check(("password", USER, GOOD_PW) in ev, "username and saved password submitted")
    check([e[1] for e in ev if e[0] == "code"] == ["000000", GOOD_CODE], "re-prompted after the wrong code")
    check(("continue",) in ev, "clicked Continue on Verification Required")
    check(("trust",) in ev, "trusted the device in Duo")
    check(("acs", "kmsi=1") in ev, "ticked 'don't show again' and said yes to Stay signed in")
    check([k for k, _ in pr] == ["code", "code"], "only prompts were the two code boxes")

    out, ev, pr = scenario("saved password is stale: asks once, saves, continues",
                           "oldpassword", [GOOD_CODE], duo="passcode")
    check(out[0] == "ok", "signed in")
    check([e[2] for e in ev if e[0] == "password"] == ["oldpassword", GOOD_PW], "retried with the new password")
    check([k for k, _ in pr] == ["password", "code"], "asked for password then code")

    out, ev, pr = scenario("cancel the code box", GOOD_PW, [None], duo="passcode")
    check(out[0] == "abort" and out[1] == 2, "cancelling the code box cancels the sign-in")
    check(not [e for e in ev if e[0] == "code"], "no code submitted")

    out, ev, pr = scenario("Microsoft error page stops the sign-in with its message",
                           GOOD_PW, [GOOD_CODE], start="/error")
    check(out[0] == "abort" and out[1] == 1 and "AADSTS90100" in out[2], "reports the AADSTS error")

    out, ev, pr = scenario("autoconnect (no interaction allowed) stops at the Duo code",
                           GOOD_PW, [GOOD_CODE], interactive=False, duo="passcode")
    check(out[0] == "abort" and out[1] == 3, "exits with 'needs you' instead of prompting")
    check(pr == [], "no prompts shown")

    out, ev, pr = scenario("push: verified push, number shown, you approve on the phone",
                           GOOD_PW, [], verified=True)
    check(out[0] == "ok", "signed in")
    check(pr == [], "nothing to type on the computer")
    shown = [n for n in NOTES if n[0] == "show"]
    check(len(shown) == 1 and shown[0][1] == "Duo code: 411" and shown[0][3], "one urgent notification: 'Duo code: 411'")
    check(NOTES[-1][0] == "close", "notification closed once signed in")
    check(("push", 1) in ev and ("trust",) in ev and ("acs", "kmsi=1") in ev, "trusted device, stayed signed in")

    out, ev, pr = scenario("push: plain push (no number)", GOOD_PW, [], push_delay=3.0)
    check(out[0] == "ok", "signed in")
    shown = [n for n in NOTES if n[0] == "show"]
    check(len(shown) == 1 and "Approve the Duo push" in shown[0][2], "says to approve the push")

    out, ev, pr = scenario("push: first one times out, it sends another", GOOD_PW, [],
                           verified=True, pushes=["timeout", "approve"])
    check(out[0] == "ok", "signed in")
    check([e for e in ev if e[0] == "push"] == [("push", 1), ("push", 2)], "sent a second push")
    shown = [n[1] for n in NOTES if n[0] == "show"]
    check(shown == ["Duo code: 411", "Duo code: 422"], "notification updated with the new number")

    out, ev, pr = scenario("push: you tap Deny", GOOD_PW, [], verified=True, pushes=["deny"])
    check(out[0] == "abort" and out[1] == 2, "denying cancels the connection")
    check([e for e in ev if e[0] == "push"] == [("push", 1)], "no second push")

    out, ev, pr = scenario("push: Duo opens on the passcode screen, it switches to push", GOOD_PW, [],
                           verified=True, duo_start="code")
    check(out[0] == "ok" and pr == [], "signed in with no code box")
    check(("push", 1) in ev, "switched to Duo Push")

    out, ev, pr = scenario("push: autoconnect doesn't buzz you", GOOD_PW, [], interactive=False)
    check(out[0] == "abort" and out[1] == 3 and not [n for n in NOTES if n[0] == "show"],
          "exits with 'needs you', no notification")

    out, ev, pr = scenario("unknown page: window shown, you click through, it hides and finishes",
                           GOOD_PW, [GOOD_CODE], notice=True, duo="passcode",
                           user_clicks="(() => { document.getElementById('gotit').click(); return 'clicked'; })()")
    check(out[0] == "ok", "signed in")
    check(any("hiding the window again" in t for t in TRACE), "window hidden again after the unknown page")
    check(("continue",) in ev and ("trust",) in ev, "rest of the flow automated")

    print("\nALL PASS" if not failures else f"\n{len(failures)} FAILED: {failures}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
