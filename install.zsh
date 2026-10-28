#!zsh
SCRIPT_PATH=${0:a:h}
YES=false
[[ "$1" == "-y" ]] && YES=true

################# GENERATE SYMLINKS TO CONFIG FILES #################
# Shell Config
echo Installing shell config...
if ! (grep -q "source $SCRIPT_PATH/.zshrc" $HOME/.zshrc)
then
    echo "[ -f $SCRIPT_PATH/.zshrc ] && source $SCRIPT_PATH/.zshrc" >> $HOME/.zshrc
    echo "[ -f $SCRIPT_PATH/.zsh_alias ] && source $SCRIPT_PATH/.zsh_alias" >> $HOME/.zshrc
    echo FPATH='$FPATH':$SCRIPT_PATH/zsh_funcs/ >> $HOME/.zshrc
else
    echo "  shell config already installed"
fi

echo Installing zsh plugins...
ln -sf $SCRIPT_PATH/.zsh_plugins.txt ~/.zsh_plugins.txt

# Vim Config
echo Installing vim config...
ln -sf $SCRIPT_PATH/.vimrc ~/.vimrc

# Git config (riff pager, git-spice forge, glab credential helper) + global ignore
echo Installing git config...
mkdir -p ~/.config/git
ln -sf $SCRIPT_PATH/.gitconfig ~/.gitconfig
ln -sf $SCRIPT_PATH/config/git/ignore ~/.config/git/ignore

# mise (tool versions: git-spice via ubi)
echo Installing mise config...
mkdir -p ~/.config/mise
ln -sf $SCRIPT_PATH/config/mise/config.toml ~/.config/mise/config.toml
if command -v mise &> /dev/null; then
    mise install -y 2>/dev/null || echo "  mise install failed (run it by hand)"
else
    echo "  mise not installed: curl https://mise.run | sh   (then rerun to get git-spice)"
fi

# Personal pixi tools: ~/pixi_tools is a symlink to the tracked directory (manifests,
# locks, scripts); the .pixi environments are built in place and gitignored.
# (pixi resolves a symlinked manifest to its real path, so per-file symlinks do not work.)
echo Installing pixi tools...
if [ -d ~/pixi_tools ] && [ ! -L ~/pixi_tools ]; then
    mv ~/pixi_tools ~/pixi_tools.bak.$(date +%Y%m%d%H%M%S) && echo "  moved existing ~/pixi_tools aside"
fi
ln -sfn $SCRIPT_PATH/pixi_tools ~/pixi_tools
for t in $SCRIPT_PATH/pixi_tools/*(/N); do
    if command -v pixi &> /dev/null && [ ! -d $t/.pixi ]; then
        (cd $t && pixi install -q) || echo "  pixi install failed for ${t:t}"
    fi
done

# Tmux Config
echo Installing tmux config...
ln -sf $SCRIPT_PATH/.tmux.conf ~/.tmux.conf

echo Installing tmux plugin manager...
[ ! -d $HOME/.tmux/plugins/tpm ] && git clone https://github.com/tmux-plugins/tpm $HOME/.tmux/plugins/tpm \
    && $HOME/.tmux/plugins/tpm/bin/install_plugins

# Code Server Config
echo Installing code-server config...
CODE_SERVER_USER_DIR="$HOME/.local/share/code-server/User"
mkdir -p "$CODE_SERVER_USER_DIR"
ln -sf $SCRIPT_PATH/code-server/settings.json "$CODE_SERVER_USER_DIR/settings.json"
ln -sf $SCRIPT_PATH/code-server/keybindings.json "$CODE_SERVER_USER_DIR/keybindings.json"

# Claude Code hooks
echo Installing Claude Code hooks...
mkdir -p ~/bin ~/.claude ~/.config

# astra now lives in its own repo at ~/src/astra.
# Install it from there: `git clone git@github.com:Astrapios/astra.git ~/src/astra
# && cd ~/src/astra && ./install.sh`. The hook symlinks below still wire Claude
# Code / Gemini to `astra hook` via the ~/bin/astra PATH wrapper its installer
# creates.

# rtk (Rust Token Killer) — reduces LLM token consumption on CLI output
if command -v rtk &> /dev/null && rtk gain &> /dev/null; then
    echo "  rtk already installed ($(rtk --version 2>/dev/null))"
else
    echo "  installing rtk..."
    curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/master/install.sh | sh
fi

# Claude settings symlinks
ln -sf $SCRIPT_PATH/scripts/claude_settings.json ~/.claude/settings.json
ln -sf $SCRIPT_PATH/scripts/claude_global.md ~/.claude/CLAUDE.md   # imports shared_rules.md, found next to the link's target
ln -sf $SCRIPT_PATH/scripts/claude_rtk.md ~/.claude/RTK.md          # imported by CLAUDE.md (@RTK.md)
ln -sf $SCRIPT_PATH/scripts/claude_keybindings.json ~/.claude/keybindings.json

# Claude hooks referenced from claude_settings.json (guard_gitlab_publish.sh etc.)
mkdir -p ~/.claude/hooks
for f in $SCRIPT_PATH/scripts/claude/hooks/*.(sh|py)(.N); do ln -sf "$f" ~/.claude/hooks/${f:t}; done

# Claude skills and agents: one symlink per skill directory / agent file, so
# skills Claude Code manages itself (~/.claude/skills/synced, .trash) stay real
# directories next to the tracked ones. A real directory already in place is
# moved aside, not merged into.
echo "  installing Claude Code skills and agents..."
mkdir -p ~/.claude/skills ~/.claude/agents
for d in $SCRIPT_PATH/scripts/claude/skills/*(/N); do
    dest=~/.claude/skills/${d:t}
    if [ -d "$dest" ] && [ ! -L "$dest" ]; then
        mv "$dest" "$dest.bak.$(date +%Y%m%d%H%M%S)"
        echo "  moved existing $dest aside"
    fi
    ln -sfn "$d" "$dest"
done
for f in $SCRIPT_PATH/scripts/claude/agents/*.md(.N); do
    ln -sf "$f" ~/.claude/agents/${f:t}
done

# Gemini settings symlink
echo "  installing Gemini CLI hooks..."
mkdir -p ~/.gemini
ln -sf $SCRIPT_PATH/scripts/gemini_settings.json ~/.gemini/settings.json

# Antigravity CLI (agy) global rules and skills, where agy is installed (the
# mkdir above makes ~/.gemini on every machine, so it is no sign of agy).
# AGENTS.md includes shared_rules.md, the file CLAUDE.md imports, so both tools
# read one copy of the rules; agy cuts a rules file at 24,000 bytes after its
# includes. agy 1.4.8 resolves that include against ~/.gemini, not next to
# AGENTS.md; the second link keeps it working if that changes. Skills are the
# Claude skills that do not need Claude-only tools.
# agy's machine-specific or secret files (antigravity-cli/settings.json with
# trustedWorkspaces, the oauth token, brain/) are never linked.
if command -v agy &> /dev/null || [ -d ~/.gemini/antigravity-cli ]; then
    echo "  installing agy rules and skills..."
    mkdir -p ~/.gemini/config/skills
    ln -sf $SCRIPT_PATH/scripts/agy_global.md ~/.gemini/config/AGENTS.md
    ln -sf $SCRIPT_PATH/scripts/shared_rules.md ~/.gemini/shared_rules.md
    ln -sf $SCRIPT_PATH/scripts/shared_rules.md ~/.gemini/config/shared_rules.md
    for s in html-report glb-view long-run deeper resolve-conflicts; do
        dest=~/.gemini/config/skills/$s
        if [ -d "$dest" ] && [ ! -L "$dest" ]; then
            mv "$dest" "$dest.bak.$(date +%Y%m%d%H%M%S)"
            echo "  moved existing $dest aside"
        fi
        ln -sfn $SCRIPT_PATH/scripts/claude/skills/$s "$dest"
    done
    agy_rules_bytes=$(( $(wc -c < $SCRIPT_PATH/scripts/agy_global.md) + $(wc -c < $SCRIPT_PATH/scripts/shared_rules.md) ))
    if (( agy_rules_bytes > 24000 )); then
        echo "  warning: agy rules are $agy_rules_bytes bytes; agy keeps the first 24,000"
    fi
fi

# Misc
echo Installing fzf...
if [ ! -d $HOME/.fzf ]
then
    git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf
fi
# ~/.fzf.zsh is sourced from the dotfiles .zshrc, so never let the installer edit ~/.zshrc
if [ ! -f $HOME/.fzf.zsh ]
then
    ~/.fzf/install --key-bindings --completion --no-update-rc
fi

################# TOOL CHECK (report only) #################
echo Checking tools used by the configs...
for tool in riff glab mise gs pixi conda nvm jq rtk astra claude tmux vim; do
    if command -v $tool &> /dev/null || type $tool &> /dev/null || { [ $tool = nvm ] && [ -s "$HOME/.nvm/nvm.sh" ]; }; then
        printf "  %-8s ok\n" $tool
    else
        case $tool in
            riff)  hint="cargo install riffdiff (git pager in .gitconfig)";;
            glab)  hint="https://gitlab.com/gitlab-org/cli/-/releases, then glab auth login";;
            mise)  hint="curl https://mise.run | sh";;
            gs)    hint="installed by mise install (git-spice)";;
            pixi)  hint="curl -fsSL https://pixi.sh/install.sh | sh";;
            conda) hint="miniforge3 installer";;
            nvm)   hint="https://github.com/nvm-sh/nvm";;
            jq)    hint="apt install jq";;
            rtk)   hint="installed above";;
            astra) hint="git clone git@github.com:Astrapios/astra.git ~/src/astra && ~/src/astra/install.sh";;
            claude) hint="npm install -g @anthropic-ai/claude-code";;
            *)     hint="apt install $tool";;
        esac
        printf "  %-8s MISSING  (%s)\n" $tool "$hint"
    fi
done

################# INSTALL SERVICES #################
if $YES; then
    "$SCRIPT_PATH/installers/install_ttyd.zsh" -y
else
    echo ""
    echo -n "Install ttyd? (y/N): "
    read answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        "$SCRIPT_PATH/installers/install_ttyd.zsh"
    fi
fi
