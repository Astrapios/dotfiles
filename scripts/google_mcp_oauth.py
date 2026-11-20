#!/usr/bin/env python3
"""Sign Claude Code in to Google's Drive and Docs MCP servers from a headless box.

Claude Code's own /mcp sign-in waits only a few minutes for the browser redirect and
asks Google for no refresh token. This script runs the same OAuth flow (PKCE, the
client in ~/.config/google-mcp/oauth_client.json) with the verifier kept on disk, so
the redirect address can come back hours later, asks for offline access (a refresh
token Claude Code then renews by itself), and writes one token for both servers into
every Claude config dir's .credentials.json (the mcpOAuth records Claude Code reads).

  python3 google_mcp_oauth.py start            print the sign-in link (valid until finish)
  python3 google_mcp_oauth.py finish '<url>'   the http://localhost:8976/callback?... address
  python3 google_mcp_oauth.py status           token expiry per config dir

Notes: /home/ubuntu/src/site3d_project_root/docs/google-docs-mcp.md
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
CFG = HOME / ".config/google-mcp"
CLIENT_JSON = Path(os.environ.get("GOOGLE_MCP_CLIENT_JSON", CFG / "oauth_client.json"))
PENDING = CFG / "pending_flow.json"
PORT = int(os.environ.get("GOOGLE_MCP_CALLBACK_PORT", "8976"))
REDIRECT = f"http://localhost:{PORT}/callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SERVERS = {
    "drive": "https://drivemcp.googleapis.com/mcp/v1",
    "docs": "https://docsmcp.googleapis.com/mcp/v1",
}
# Union of the scopes the two servers advertise (/.well-known/oauth-protected-resource).
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


def client():
    d = json.loads(CLIENT_JSON.read_text())
    c = d.get("web") or d.get("installed") or d
    return c["client_id"], c["client_secret"]


def write_private(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def config_dirs():
    dirs = [HOME / ".claude"]
    dirs += sorted(p for p in HOME.glob(".claude-*") if p.is_dir() and (p / ".claude.json").exists())
    return dirs


def server_key(name, url):
    # Claude Code's record key: name|sha256(JSON of {type,url,headers})[:16]
    blob = json.dumps({"type": "http", "url": url, "headers": {}}, separators=(",", ":"))
    return f"{name}|{hashlib.sha256(blob.encode()).hexdigest()[:16]}"


def start():
    cid, _ = client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    write_private(PENDING, {"verifier": verifier, "state": state, "created": time.time()})
    q = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": REDIRECT,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    print(AUTH_URL + "?" + urllib.parse.urlencode(q))


def finish(callback_url):
    pending = json.loads(PENDING.read_text())
    q = urllib.parse.parse_qs(urllib.parse.urlparse(callback_url).query)
    if q.get("state", [""])[0] != pending["state"]:
        sys.exit("state does not match the pending sign-in: run start again")
    if "code" not in q:
        sys.exit(f"no code in the address: {q.get('error', ['?'])[0]}")
    cid, secret = client()
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": q["code"][0],
        "redirect_uri": REDIRECT,
        "client_id": cid,
        "client_secret": secret,
        "code_verifier": pending["verifier"],
    }).encode()
    try:
        tok = json.load(urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=30))
    except urllib.error.HTTPError as e:
        sys.exit(f"token exchange failed: {e.code} {e.read().decode()[:300]}")
    if "refresh_token" not in tok:
        print("warning: no refresh token returned; access ends in about an hour")
    expires_ms = int((time.time() + tok.get("expires_in", 3599)) * 1000)
    for d in config_dirs():
        path = d / ".credentials.json"
        creds = json.loads(path.read_text()) if path.exists() else {}
        recs = creds.setdefault("mcpOAuth", {})
        for name, url in SERVERS.items():
            key = server_key(name, url)
            rec = {
                "serverName": name,
                "serverUrl": url,
                "accessToken": tok["access_token"],
                "expiresAt": expires_ms,
                "scope": tok.get("scope", " ".join(SCOPES)),
                "clientId": cid,
                "clientSecret": secret,
            }
            if tok.get("refresh_token"):
                rec["refreshToken"] = tok["refresh_token"]
            recs[key] = rec
        write_private(path, creds)
        print(f"wrote drive and docs tokens to {path}")
    PENDING.unlink()


def status():
    for d in config_dirs():
        path = d / ".credentials.json"
        recs = json.loads(path.read_text()).get("mcpOAuth", {}) if path.exists() else {}
        for name, url in SERVERS.items():
            r = recs.get(server_key(name, url))
            if not r or not (r.get("accessToken") or r.get("refreshToken")):
                print(f"{d}: {name}: not signed in")
                continue
            left = (r.get("expiresAt", 0) / 1000 - time.time()) / 60
            print(f"{d}: {name}: access token {left:.0f} min left, refresh token {'yes' if r.get('refreshToken') else 'no'}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"start": start, "status": status}.get(cmd, lambda: finish(sys.argv[2]))() if cmd != "finish" else finish(sys.argv[2])
