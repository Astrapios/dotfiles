# ttyd: built from source with local patches

`install_ttyd.zsh` no longer downloads the upstream release binary. It calls
`build_ttyd.zsh`, which builds ttyd from upstream `main` (pinned commit) plus
the patches in `patches/`. Everything here exists so that copying text out of
tmux inside the web terminal lands on the system clipboard, including on iOS.

## Why each piece exists

1. **Build from `main`, not the 1.7.7 release.** OSC 52 clipboard support
   (xterm.js clipboard addon) was added upstream in commit `b1eaaee` after the
   last release. Without it the browser silently ignores OSC 52.
2. **`.tmux.conf`: `Ms` override.** tmux's stock capability emits
   `ESC ] 52 ; ; <b64> BEL` with an empty selection field; the xterm.js addon
   only honours `c`. The override forces `c` and must reference both `%p1` and
   `%p2`, otherwise tmux 3.4 (`tiparm_s`) refuses to expand it and emits nothing.
   The drag-release copy binding and `set-clipboard on` live there too.
3. **`patches/0001`: WebKit clipboard provider.** Safari and every iOS browser
   refuse `navigator.clipboard.writeText()` outside a user gesture. The patch
   arms a promise-backed `clipboard.write()` on each key/pointer gesture and
   resolves it when OSC 52 arrives; abandoned arms reject without touching the
   pasteboard. If a write is still refused, a "Tap to copy" button appears.
   Chrome/Firefox are unaffected (they allow the plain write).
4. **`patches/0002`: ctrl-c on iOS hardware keyboards.** Safari delivers ctrl-c
   from a Bluetooth keyboard as keyCode 13 (Enter) with key `c`; xterm.js < 7
   sends CR. The patch sends ETX and swallows the event. Add `?keydebug` to the
   URL to see raw key event shapes in an overlay.
5. **libwebsockets built static with libuv.** ttyd wants libwebsockets with the
   libuv event loop. Building a pinned v4.3-stable copy into
   `~/src/build-ttyd/prefix` avoids depending on whatever the distro ships, and
   the resulting ttyd only needs libz, libjson-c, libuv, libssl and libcrypto
   from the system.

Secure context still applies: `navigator.clipboard` exists only on HTTPS or
`localhost`, so the web terminal must be reached over HTTPS (or an SSH tunnel).

## Updating

- Upstream moved and you want to follow: `TTYD_REF=main build_ttyd.zsh`. If
  `git am` fails, fix conflicts in `~/src/build-ttyd/ttyd`, then regenerate the
  patches (below) and bump `TTYD_REF` in `build_ttyd.zsh` to the new base.
- You changed the frontend in `~/src/build-ttyd/ttyd` and want to keep it:

  ```sh
  cd ~/src/build-ttyd/ttyd
  git format-patch -o ~/.dotfiles/installers/ttyd/patches <base-sha>..HEAD -- . ':!src/html.h'
  ```

  `src/html.h` is excluded on purpose; the build regenerates it from the
  TypeScript sources.
- xterm.js 7.x includes the ctrl-c fix natively; drop `0002` once ttyd upgrades.

## Rollback

`build_ttyd.zsh` keeps the previous binary as `/usr/local/bin/ttyd.<version>`:

```sh
sudo cp /usr/local/bin/ttyd.<version> /usr/local/bin/ttyd && sudo systemctl restart ttyd
```
