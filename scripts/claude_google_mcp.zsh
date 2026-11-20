#!/usr/bin/env zsh
# Register Google's official Workspace MCP servers (Drive, Docs) with Claude Code
# at user scope, in every Claude config dir on this box (~/.claude.json for the
# default dir, <dir>/.claude.json for each ~/.claude-* CLAUDE_CONFIG_DIR), so every
# area, desk and worker session gets them.
#
# Secrets stay out of git: the OAuth client (the JSON Google Cloud Console
# downloads for a Web or Desktop client) lives at
#   ~/.config/google-mcp/oauth_client.json   (mode 600)
# Tokens are written by Claude Code itself into <config dir>/.credentials.json
# after the first sign-in (/mcp -> drive -> Authenticate).
#
# Usage: zsh claude_google_mcp.zsh [--dry-run]
# Guide: https://developers.google.com/workspace/guides/configure-mcp-servers
# Notes: /home/ubuntu/src/site3d_project_root/docs/google-docs-mcp.md

emulate -L zsh
setopt pipe_fail

CLIENT_JSON=${GOOGLE_MCP_CLIENT_JSON:-$HOME/.config/google-mcp/oauth_client.json}
# Claude Code's OAuth redirect is http://localhost:<port>/callback; a fixed port
# lets a Web client register it. Desktop clients accept any loopback port.
CALLBACK_PORT=${GOOGLE_MCP_CALLBACK_PORT:-8976}
typeset -A SERVERS=(
    drive https://drivemcp.googleapis.com/mcp/v1
    docs  https://docsmcp.googleapis.com/mcp/v1
)
DRY=0; [[ $1 == --dry-run ]] && DRY=1

if [[ ! -r $CLIENT_JSON ]]; then
    echo "google-mcp: no OAuth client at $CLIENT_JSON, skipping (see docs/google-docs-mcp.md)"
    exit 0
fi
chmod 600 $CLIENT_JSON
# Accept Google's download format ({"web": {...}} or {"installed": {...}}) or a flat object.
read -r CLIENT_ID CLIENT_SECRET < <(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
c = d.get("web") or d.get("installed") or d
print(c["client_id"], c["client_secret"])' $CLIENT_JSON) || { echo "google-mcp: cannot parse $CLIENT_JSON"; exit 1; }

# Config dirs: the default (~/.claude) plus every ~/.claude-* that has its own .claude.json.
dirs=($HOME/.claude)
for d in $HOME/.claude-*(/N); do [[ -f $d/.claude.json ]] && dirs+=$d; done

for dir in $dirs; do
    if [[ $dir == $HOME/.claude ]]; then envset=(env -u CLAUDE_CONFIG_DIR); else envset=(env CLAUDE_CONFIG_DIR=$dir); fi
    for name url in ${(kv)SERVERS}; do
        echo "google-mcp: $dir: $name -> $url"
        (( DRY )) && continue
        $envset claude mcp remove -s user $name >/dev/null 2>&1
        $envset MCP_CLIENT_SECRET=$CLIENT_SECRET claude mcp add -s user --transport http \
            --client-id $CLIENT_ID --client-secret --callback-port $CALLBACK_PORT \
            $name $url </dev/null || echo "google-mcp: add $name failed in $dir"
    done
done
