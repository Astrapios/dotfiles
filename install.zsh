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

# Claude Code / Telegram hooks
echo Installing Claude Code hooks...
mkdir -p ~/bin ~/.claude ~/.config

# Pixi + global python with requests
if ! command -v pixi &> /dev/null; then
    echo "  installing pixi..."
    curl -fsSL https://pixi.sh/install.sh | bash
fi
echo "  installing tg-hook..."
(cd $SCRIPT_PATH/scripts/tg-hook && pixi install)
cat > ~/bin/tg-hook << WRAPPER
#!/bin/sh
exec $SCRIPT_PATH/scripts/tg-hook/.pixi/envs/default/bin/tg-hook "\$@"
WRAPPER
chmod +x ~/bin/tg-hook

# Claude settings symlinks
ln -sf $SCRIPT_PATH/scripts/claude_settings.json ~/.claude/settings.json
ln -sf $SCRIPT_PATH/scripts/claude_global.md ~/.claude/CLAUDE.md

# Telegram credentials
if [ ! -f ~/.config/tg_hook.env ]; then
    if $YES; then
        echo "  skipping Telegram credentials (set manually in ~/.config/tg_hook.env)"
    else
        echo -n "  Telegram Bot Token (or Enter to skip): "
        read tg_token
        if [ -n "$tg_token" ]; then
            echo -n "  Telegram Chat ID: "
            read tg_chat
            printf "TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n" "$tg_token" "$tg_chat" > ~/.config/tg_hook.env
            chmod 600 ~/.config/tg_hook.env
            echo "  saved to ~/.config/tg_hook.env"
        fi
    fi
else
    echo "  Telegram credentials already configured"
fi

# Misc
echo Installing fzf...
if [ ! -d $HOME/.fzf ]
then
    git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf
fi
if ! (grep -q "fzf" $HOME/.zshrc)
then
    ~/.fzf/install --key-bindings --completion --update-rc
fi

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
