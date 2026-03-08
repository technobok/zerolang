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
