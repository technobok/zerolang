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
require("zerolang.lsp").setup({
    cmd = { zerolang .. "/bin/zls", "--stdio" },
    systemDir = zerolang .. "/lib/system",
})
```

### lazy.nvim

lazy loads plugins *after* `init.lua` runs, so call `setup` from the plugin's
`config` — a bare top-level `require("zerolang.lsp")` would run too early and
fail with `module not found`:

```lua
{
    dir = "~/path/to/zerolang/editor/nvim",
    config = function()
        local zerolang = vim.fn.expand("~/path/to/zerolang")
        require("zerolang.lsp").setup({
            cmd = { zerolang .. "/bin/zls", "--stdio" },
            systemDir = zerolang .. "/lib/system",
        })
    end,
}
```

`zls` then starts automatically for every `zerolang` buffer — no other plugins
required. `systemDir` points at the zerolang standard library (or set
`$ZEROLANG_SYSTEM`); `srcDir` is optional (it defaults to the workspace root
`zls` detects). See `docs/zls.pdoc` for the full protocol contract.
