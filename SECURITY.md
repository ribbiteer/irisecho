# Security

## Reporting a vulnerability

Please report privately through GitHub: open the repository's **Security**
tab and choose **Report a vulnerability**. Do not open a public issue.

Expect an acknowledgement within a week. Fixes land on the main branch and
in the next release, with credit if you want it.

## Scope worth knowing

- IrisEcho binds to `127.0.0.1` by default. LAN access is opt-in and requires
  a pairing token; anything that bypasses that token is in scope.
- While it runs, IrisEcho writes its port and per-run session token to
  `server.json` in the data folder. Its own command line uses that to hand work
  to the running app, and so can scripts and coding agents you run yourself
  (see [AGENTS.md](core/src/irisecho_core/AGENTS.md)). A program running as you
  can already read your files, so this is the same boundary; anything that lets
  another user of the computer, or a web page, get the token is in scope.

## How phone and LAN access works

Off by default (Settings, Phone and network access). When switched on, a
second listener accepts connections from the local network, on port 7789 when
it is free.

- Nothing on it works until a device pairs. Settings shows a code (also as a QR
  code) that is single use and valid for five minutes. Five wrong codes lock
  pairing for a minute, for everyone.
- Pairing gives the device its own random cookie (HttpOnly, SameSite=Strict).
  Only a hash of it is stored, in `devices.json` in the data folder. Devices
  are listed in Settings and can be revoked; revoking ends their access and
  closes their live connection at once.
- The computer's own session cookie is worthless on the network listener, and
  the network listener never hands it out.
- A paired device may only make things and browse the library: start, cancel,
  favourite and delete jobs, upload reference files, read results and
  progress. Settings, folders, model setup, licence acceptance, tokens and
  the pairing controls answer `403` there. Folder paths are left out of what a
  device is sent.
- Requests must name an address, a bare machine name or a `.local` name in the
  `Host` header, which blocks DNS rebinding through public names.
- The connection is plain HTTP. Anyone who can watch traffic on the network
  can see what a paired device sees, including its cookie. Use it on a network
  you trust; turning the switch off closes the port.
- IrisEcho downloads and runs third-party engines and model weights. Issues
  in how it fetches, verifies or isolates them are in scope; vulnerabilities
  in the upstream projects themselves belong with those projects.
- Access tokens for gated model hosts are stored in the operating system's
  keychain. Any path that writes them elsewhere is in scope.
