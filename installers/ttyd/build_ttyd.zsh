#!/bin/zsh
# Build ttyd from upstream source plus the local patches in ./patches, and
# (unless --no-install) install it over /usr/local/bin/ttyd with a rollback copy.
#
# Why not the release binary: see README.md next to this script. Short version:
# OSC 52 clipboard support only exists on upstream main (unreleased), and the
# patches make it work on WebKit/iOS and fix ctrl-c on iOS hardware keyboards.
#
# Usage:
#   build_ttyd.zsh                # build + sudo install + restart ttyd.service
#   build_ttyd.zsh --no-install   # build only; binary path printed at the end
#   TTYD_REF=<sha|branch> build_ttyd.zsh   # build a different upstream ref
#
# Requirements: apt build deps (installed below), and node >= 18 on PATH
# (nvm is fine); yarn comes via corepack, nothing global is installed.
set -euo pipefail

HERE=${0:a:h}
TTYD_REF=${TTYD_REF:-2922cb8}          # upstream commit the patches apply to cleanly
LWS_REF=${LWS_REF:-v4.3-stable}        # libwebsockets branch (static, with libuv)
WORK=${TTYD_BUILD_DIR:-$HOME/src/build-ttyd}
INSTALL=true
for a in "$@"; do
    case $a in
        --no-install) INSTALL=false ;;
        *) echo "unknown argument: $a" >&2; exit 2 ;;
    esac
done
JOBS=$(nproc 2>/dev/null || echo 4)

log() { print -P "%F{cyan}==>%f $*"; }

# --- 0. dependencies -------------------------------------------------------
DEPS=(build-essential cmake git pkg-config libjson-c-dev libuv1-dev libssl-dev zlib1g-dev)
MISSING=()
for d in $DEPS; do dpkg -s $d >/dev/null 2>&1 || MISSING+=($d); done
if (( ${#MISSING} )); then
    log "installing apt packages: $MISSING"
    sudo apt-get install -y $MISSING
fi
if ! command -v node >/dev/null; then
    echo "node >= 18 is required to build the ttyd web frontend (install nvm + node first)" >&2
    exit 1
fi
if ! command -v corepack >/dev/null; then
    echo "corepack (ships with node >= 16) not found on PATH" >&2
    exit 1
fi
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0

mkdir -p $WORK
cd $WORK

# --- 1. libwebsockets: static, libuv event loop, no test apps --------------
if [[ ! -d libwebsockets ]]; then
    log "cloning libwebsockets $LWS_REF"
    git clone -q --depth 1 -b $LWS_REF https://github.com/warmcat/libwebsockets.git
fi
if [[ ! -f prefix/lib/libwebsockets.a ]]; then
    log "building libwebsockets"
    cmake -S libwebsockets -B libwebsockets/build -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=$WORK/prefix \
        -DLWS_WITH_LIBUV=ON -DLWS_WITH_EVLIB_PLUGINS=ON \
        -DLWS_WITH_STATIC=ON -DLWS_WITH_SHARED=OFF \
        -DLWS_WITH_SSL=ON -DLWS_WITH_ZLIB=OFF -DLWS_IPV6=ON -DLWS_UNIX_SOCK=ON \
        -DLWS_WITHOUT_TESTAPPS=ON -DLWS_WITHOUT_TEST_SERVER=ON \
        -DLWS_WITHOUT_TEST_CLIENT=ON -DLWS_WITHOUT_TEST_PING=ON >/dev/null
    cmake --build libwebsockets/build -j$JOBS >/dev/null
    cmake --install libwebsockets/build >/dev/null
    # The exported config lists both static and shared targets; only static exists.
    sed -i 's/^set(LIBWEBSOCKETS_LIBRARIES .*/set(LIBWEBSOCKETS_LIBRARIES websockets)/' \
        prefix/lib/cmake/libwebsockets/libwebsockets-config.cmake
fi

# --- 2. ttyd source at the pinned ref, plus our patches --------------------
if [[ ! -d ttyd ]]; then
    log "cloning ttyd"
    git clone -q https://github.com/tsl0922/ttyd.git
fi
cd ttyd
git fetch -q origin
log "checking out $TTYD_REF and applying $(ls $HERE/patches/*.patch | wc -l) patches"
git checkout -q -B build $TTYD_REF
git -c user.name=ttyd-build -c user.email=ttyd-build@localhost am -q --3way $HERE/patches/*.patch

# --- 3. web frontend (regenerates src/html.h) -------------------------------
log "building web frontend"
(cd html && corepack yarn install >/dev/null 2>&1 && corepack yarn build 2>&1 | grep -E 'ERROR|compiled' || true)
git diff --quiet -- src/html.h && { echo "src/html.h did not change; frontend build failed?" >&2; exit 1; }

# --- 4. ttyd binary ----------------------------------------------------------
log "building ttyd"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=$WORK/prefix >/dev/null
cmake --build build -j$JOBS >/dev/null
VERSION=$(./build/ttyd --version | awk '{print $3}')
OUT=$WORK/ttyd-$VERSION-patched
install -m755 build/ttyd $OUT
log "built $OUT ($VERSION)"

# --- 5. install ---------------------------------------------------------------
if $INSTALL; then
    if [[ -x /usr/local/bin/ttyd ]]; then
        OLD=$(/usr/local/bin/ttyd --version 2>/dev/null | awk '{print $3}')
        sudo cp /usr/local/bin/ttyd /usr/local/bin/ttyd.${OLD:-old}
        log "previous binary kept as /usr/local/bin/ttyd.${OLD:-old}"
    fi
    sudo install -m755 $OUT /usr/local/bin/ttyd
    if systemctl is-enabled ttyd >/dev/null 2>&1; then
        sudo systemctl restart ttyd
        log "ttyd.service restarted; reload browser tabs to pick up the new frontend"
    fi
fi
