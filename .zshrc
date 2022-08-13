# need to initilaize compinit first for fzf-tab to work
autoload -Uz compinit && compinit

# clone antidote if necessary
[[ -e ~/.antidote ]] || git clone https://github.com/mattmc3/antidote.git ~/.antidote

# source antidote
. ~/.antidote/antidote.zsh

antidote load

# load zsh prompt
autoload -Uz promptinit && promptinit && prompt pure

export EDITOR='vim'
export VISUAL='vim'

# THIS FIXES CTRL P, CTRL N COMMAND HISTORY SCROLLING IN TMUX
bindkey -e

# Enable loading editor
autoload edit-command-line
zle -N edit-command-line
bindkey '^Xe' edit-command-line

# MAKE cd BEHAVE LIKE pushd, ALLOWING MOVING BACK TO PREVIOUS DIRECTORY USING popd
setopt auto_pushd

# ALLOW COLORED OUTPUT FOR LS
export CLICOLOR=1
export LSCOLORS=GxBxhxDxfxhxhxhxhxcxcx

# FIXES TAB COMPLETION COLORS TO MATCH COLORS FROM LS OUTPUT
# convert LSCOLORS to LS_COLORS format at https://geoff.greer.fm/lscolors/
export LS_COLORS="di=1;36:ln=1;31:so=37:pi=1;33:ex=35:bd=37:cd=37:su=37:sg=37:tw=32:ow=32"
zstyle ':completion:*:default' list-colors ${(s.:.)LS_COLORS}

# 10 ms for key time sequences, reduces ESC key delays
KEYTIMEOUT=1

# PREVENTS SAVING COMMANDS THAT BEGIN WITH SPACE
HISTFILE=~/.zsh_history
HISTSIZE=500000
SAVEHIST=500000
setopt appendhistory
setopt INC_APPEND_HISTORY  
setopt SHARE_HISTORY
setopt HIST_IGNORE_SPACE
setopt HIST_IGNORE_ALL_DUPS

# Change DIRECTORIES WITHOUT cd
setopt auto_cd

# custom functions. These functions are in ./zsh_funcs/<function_name> files
autoload -Uz fullpath

export PATH="$PATH:$HOME/bin/"
