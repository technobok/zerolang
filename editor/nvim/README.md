# Zerolang syntax highlighting for Neovim

Provides syntax highlighting for `.z` files.

## Installation

### Manual

Copy (or symlink) the `syntax/` and `ftdetect/` directories into your Neovim config:

```sh
cp -r syntax ftdetect ~/.config/nvim/
```

### lazy.nvim

```lua
{
    dir = "~/path/to/zerolang/editor/nvim",
}
```

### vim-plug

```vim
Plug '~/path/to/zerolang/editor/nvim'
```

### packer.nvim

```lua
use { "~/path/to/zerolang/editor/nvim" }
```

## LSP (language server)

Build the server:

```sh
make bin/zls
```

`require("zerolang.lsp")` needs this `editor/nvim` directory on Neovim's
runtimepath (otherwise you get `module 'zerolang.lsp' not found`). Pick whichever
matches your setup.

### Standalone (no plugin manager)

Put this in `init.lua` — it adds the directory to the runtimepath itself, so it
works even without installing the syntax plugin above:

```lua
local zerolang = vim.fn.expand("~/path/to/zerolang") -- your checkout
vim.opt.runtimepath:prepend(zerolang .. "/editor/nvim")
require("zerolang.lsp").setup()
```

### lazy.nvim

lazy loads plugins *after* `init.lua` runs, so call `setup` from the plugin's
`config` — a bare top-level `require("zerolang.lsp")` would run too early and
fail with `module not found`:

```lua
{
    dir = "~/path/to/zerolang/editor/nvim",
    config = function()
        require("zerolang.lsp").setup()
    end,
}
```

`zls` then starts automatically for every `zerolang` buffer — no other plugins
required, and `setup()` needs no paths: it locates `bin/zls` and `lib/system`
relative to this checkout. Pass `cmd` / `systemDir` (or set `$ZEROLANG_SYSTEM`)
to override; `srcDir` is optional and defaults to the workspace root `zls`
detects.

Completion autotriggers on `.`: typing a member access opens a popup of the
value's fields and methods (or a unit's top-level symbols), driven by Neovim's
built-in LSP completion. `setup()` sets `completeopt` to `menuone,noselect,popup`
buffer-locally (the global default is left alone) so the menu opens without
auto-selecting the first item and further typing filters it. Pass
`completion = false` to `setup()` to leave completion — and `completeopt` — to
your own engine (nvim-cmp, blink.cmp, …).

One `zls` process serves a whole workspace: Neovim dedups clients by `root_dir`,
so every `.z` buffer under the same root shares a single server, which checks
the open buffers layered over the on-disk sources. Files in unrelated
directories get their own root — and their own server. See `docs/zls.pdoc` for
the full protocol contract.
