" Import plugins and set their appropriate settings
"https://github.com/rhysd/vim-grammarous {{{
scriptencoding utf-8
set nocompatible

" install vim-plug if it does not exist
if empty(glob('~/.vim/autoload/plug.vim'))
  silent !curl -fLo ~/.vim/autoload/plug.vim --create-dirs
    \ https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
  autocmd VimEnter * PlugInstall --sync | source $MYVIMRC
endif
call plug#begin('~/.vim/plugins')

"-------------------------------------------------
"|         Vim global search and replace          |
"-------------------------------------------------
Plug 'mileszs/ack.vim'
if executable('ag')
    let g:ackprg = 'ag --vimgrep'
endif
" for multiple file search and replace,
" :Ack <pattern>
" :cdo s/<pattern>/<newpattern>/g | update
"
" <pattern> can be simple word, or regex expression, in which case
" it should be enclosed by single quotes 'pattern'. :cdo performs
" action on quickfix lists. 'update' saves files that are changed.

" FZF
Plug 'junegunn/fzf', { 'do': { -> fzf#install() } }

"-------------------------------------------------
"|        Syntax Check and Autocompletion        |
"-------------------------------------------------
" Asynchronous Linting engine
" Need to install linters using pip3:
" pip3 install proselint
" pip3 install vim-vint
" pip3 install autopep8
Plug 'w0rp/ale', {'for' : ['tex','python','vim']}
let g:ale_linters = {
            \ 'tex': ['proselint'],
            \ 'python': ['autopep8'],
            \ 'vim': ['vint'],
            \ }

" for easy alignment of equal signs, tables, or to a symbol
Plug 'godlygeek/tabular'

Plug 'octol/vim-cpp-enhanced-highlight', {'for': ['cpp']}
let g:cpp_class_scope_highlight = 1
let g:cpp_member_variable_highlight = 1
let g:cpp_class_decl_highlight = 1
let g:cpp_experimental_template_highlight = 1
let g:cpp_concepts_highlight = 1
let c_no_curly_error=1

"-------------------------------------------------
"|               Writing Related                 |
"-------------------------------------------------
"GoYo, gets rid of dictractions
Plug 'junegunn/goyo.vim', {'on': ['Goyo']}

"-------------------------------------------------
"|               Vim Status Bar                  |
"-------------------------------------------------
"- Colorful vim status bar
Plug 'vim-airline/vim-airline'
Plug 'vim-airline/vim-airline-themes'
let g:airline#extensions#tabline#enabled = 0
let g:airline_powerline_fonts = 1
let g:airline_theme='quantum'

"-------------------------------------------------
"|       Filetype Specific Syntax and Tools      |
"-------------------------------------------------
" colorize hex numbers
Plug 'chrisbra/Colorizer'

"-------------------------------------------------
"|         Vim File Tree/Search/Management       |
"-------------------------------------------------
Plug 'tpope/vim-fugitive'
Plug 'scrooloose/nerdtree', {'on': ['NERDTreeToggle']}
Plug 'airblade/vim-gitgutter'

"-------------------------------------------------
"|                 Vim Color Themes              |
"-------------------------------------------------
Plug 'tyrannicaltoucan/vim-quantum'
let g:quantum_black=1
let g:quantum_italics=1

"-------------------------------------------------
"|                     Misc.                     |
"-------------------------------------------------
" Log file coloring
Plug 'mtdl9/vim-log-highlighting'

" Navigate between vim and tmux
Plug 'christoomey/vim-tmux-navigator'

" Automatic parenthesis, quotes, etc. completion
Plug 'Raimondi/delimitMate'

" Colorful parenthesis/brackets for easy identification
Plug 'luochen1990/rainbow'
let g:rainbow_active = 1

call plug#end()
" }}}

" Vim general settings
" {{{

" use 'ag' over grep
if executable('rg')
    set grepprg=rg
endif

"fortran specific
let fortran_free_source=1
let fortran_do_enddo=1

"terminal colors
set t_Co=256

"use conceal to replace keywords to symbols, etc.
set conceallevel=2

"update jobs (such as git gutter) after inactivity of 'updatetime'
set updatetime=100

" print options for :hardcopy
set printoptions=paper:letter,number:n,left:1in,right:1in,top:1in:bottom:1in

" access system clipboard
set clipboard=unnamed

" ignorecase for search
set ignorecase 

" use \c for case sensitve search or use capital letters in search
" for case sensitive search
set smartcase

" set fold method for easy overview
set foldmethod=marker

" Backup and swap file generation folder. Separated by ',' and listed as order
" of trial. These folders have to actually exist for vim to generate file
" there.
let tmppath = expand('~/.vim/vimtmp/')    " create the temp file storage dir
" if the location does not exist.
if !isdirectory(tmppath)
    call system('mkdir -p ' . tmppath)
endif    " point Vim to the defined undo directory.

set backup
set backupdir=tmppath,/private/tmp,.
set directory=tmppath,/private/tmp,.

" Persistent undofile
" guard for distributions lacking the 'persistent_undo' feature.
if has('persistent_undo')
    " define a path to store persistent undo files.
    set undodir=tmppath,/private/tmp,.    " finally, enable undo persistence.
    set undofile
endif

set background=dark
set hlsearch

set termguicolors
colorscheme quantum

" change some highlights, check highlight under :syntax for details
highlight Search    guibg=#d5b875 guifg=#000000 gui=bold cterm=italic,bold
highlight Visual    guibg=#d5b875 guifg=#000000 gui=bold cterm=bold

" highlight fix for special characters concealed by markup packpages, such as
"pandoc, markdown, latex
hi! link Conceal Special

" set Vim-specific sequences for RGB colors that works in Tmux
let &t_8f = "\<Esc>[38;2;%lu;%lu;%lum"
let &t_8b = "\<Esc>[48;2;%lu;%lu;%lum"

" set correct escape sequences for italics
let &t_ZH="\e[3m"
let &t_ZR="\e[23m"

" Eliminating delays on ESC
set timeoutlen=1000 ttimeoutlen=0

" enable mouse
set mouse=a

" show line numbers
set number

" show line numbers when printed
set printoptions=number:y

" set tabs to have 4 spaces
set tabstop=4

" indent when moving to the next line while writing code
set autoindent

" expand tabs into spaces
set expandtab

" when using the >> or << commands, shift lines by 4 spaces
set shiftwidth=4

" show a visual line under the cursor's current line 
set cursorline

" show the matching part of the pair for [] {} and ()
set showmatch

" automatically change window's cwd to file's dir
set autochdir

" I'm prefer spaces to tabs
set tabstop=4
set expandtab
set backspace=indent,eol,start

" more subtle popup colors 
if has ('gui_running')
    set guifont=Source\ Code\ Pro\ for\ Powerline:h18
    highlight Pmenu guibg=#cccccc gui=bold    
    endif

" }}}

" Mappings
" {{{

" Map leader key to comma
let g:mapleader = ','
let g:maplocalleader = '\'

" disable arrow keys!
inoremap  <Up>     <NOP>
inoremap  <Down>   <NOP>
inoremap  <Left>   <NOP>
inoremap  <Right>  <NOP>
noremap   <Up>     <NOP>
noremap   <Down>   <NOP>
noremap   <Left>   <NOP>
noremap   <Right>  <NOP>

" split navigations
nnoremap <C-J> <C-W><C-J>
nnoremap <C-K> <C-W><C-K>
nnoremap <C-L> <C-W><C-L>
nnoremap <C-H> <C-W><C-H>

" easier navigation in lines that span more than a single row
nnoremap k gk
nnoremap j gj
nnoremap 0 g0
nnoremap $ g$
nnoremap ^ g^

" clear highlight in search
nnoremap <leader><space> :nohlsearch<CR>

" Easy access to vimrc, and reload it
nnoremap <leader>ev :vsplit $MYVIMRC<CR>
nnoremap <silent> <leader>sv :so $MYVIMRC<CR>

" Use <leader>l to toggle display of whitespace
nnoremap <leader>l :set list!<CR>

" change to upper case while typing
inoremap <c-u> <esc>viwUea

" Toggle NERDTree and Tagbar
noremap <C-n> :NERDTreeToggle<CR>
nnoremap <leader>tt :TagbarToggle<CR>

" Window Splitting, closing, etc
nnoremap <silent> <leader>v :vsplit<CR>
nnoremap <silent> <leader>h :split<CR>
nnoremap <silent> <leader>q :close<CR>
noremap <leader>= <C-w>=
noremap <silent> <leader>z :tab split<CR>

" Tabular Alignment
nnoremap <silent> <leader>a :Tab /
vnoremap <silent> <leader>a :Tabularize /

" Enable folding with the spacebar
nnoremap za <nop>
nnoremap <space> za

" Make H and L more useful
nnoremap H ^
nnoremap L $

" Syntax Check toggle
noremap <leader>sn :SyntasticCheck<CR>
noremap <leader>ac :SyntasticToggleMode<CR>

" Grammar Checking
noremap <leader>ch :GrammarousCheck<CR>

" Git Command Shortcuts
noremap <leader>gs :Git<CR>
noremap <leader>gb :Git Blame<CR>

" this fixes crontab edit in mac OSX
au BufEnter /private/tmp/crontab.* setl backupcopy=yes
" }}}
