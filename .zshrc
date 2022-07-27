# clone antidote if necessary
[[ -e ~/.antidote ]] || git clone https://github.com/mattmc3/antidote.git ~/.antidote

# source antidote
. ~/.antidote/antidote.zsh

zstyle ':autocomplete:*' default-context ''
# '': Start each new command line with normal autocompletion.
# history-incremental-search-backward: Start in live history search mode.

zstyle ':autocomplete:*' min-input 2  # int
# Wait until this many characters have been typed, before showing completions.

zstyle ':autocomplete:*' ignored-input '' # extended glob pattern
# '':     Always show completions.
# '..##': Don't show completions when the input consists of two or more dots.

zstyle ':autocomplete:*' list-lines 16  # int
# If there are fewer than this many lines below the prompt, move the prompt up
# to make room for showing this many lines of completions (approximately).

zstyle ':autocomplete:history-search:*' list-lines 16  # int
# Show this many history lines when pressing ↑.

zstyle ':autocomplete:history-incremental-search-*:*' list-lines 16  # int
# Show this many history lines when pressing ⌃R or ⌃S.

zstyle ':autocomplete:*' recent-dirs zsh-z
# cdr:  Use Zsh's `cdr` function to show recent directories as completions.
# no:   Don't show recent directories.
# zsh-z|zoxide|z.lua|z.sh|autojump|fasd: Use this instead (if installed).
# ⚠️ NOTE: This setting can NOT be changed at runtime.

zstyle ':autocomplete:*' insert-unambiguous no
# no:  Tab inserts the top completion.
# yes: Tab first inserts a substring common to all listed completions, if any.

zstyle ':autocomplete:*' widget-style menu-select
# complete-word: (Shift-)Tab inserts the top (bottom) completion.
# menu-complete: Press again to cycle to next (previous) completion.
# menu-select:   Same as `menu-complete`, but updates selection in menu.
# ⚠️ NOTE: This setting can NOT be changed at runtime.

zstyle ':autocomplete:*' fzf-completion yes
# no:  Tab uses Zsh's completion system only.
# yes: Tab first tries Fzf's completion, then falls back to Zsh's.
# ⚠️ NOTE: This setting can NOT be changed at runtime and requires that you
# have installed Fzf's shell extensions.

antidote load

# load zsh prompt
autoload -Uz promptinit && promptinit && prompt pure

export EDITOR='vim'
export VISUAL='vim'

# THIS FIXES CTRL P, CTRL N COMMAND HISTORY SCROLLING IN TMUX
bindkey -e

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

# Enable vim mode
bindkey -v
