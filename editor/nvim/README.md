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

Then opt in from your config (once this plugin is on the runtimepath, so
`zerolang.lsp` is `require`-able). It starts `zls` for every `zerolang` buffer —
no other plugins required:

```lua
local zerolang = "~/path/to/zerolang" -- your checkout
require("zerolang.lsp").setup({
    cmd = { vim.fn.expand(zerolang .. "/bin/zls"), "--stdio" },
    systemDir = vim.fn.expand(zerolang .. "/lib/system"),
})
```

`systemDir` points at the zerolang standard library (or set `$ZEROLANG_SYSTEM`).
`srcDir` is optional — it defaults to the workspace root `zls` detects. See
`docs/zls.pdoc` for the full protocol contract.
