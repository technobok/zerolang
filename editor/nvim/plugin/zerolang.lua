-- Register the zerolang filetype with Neovim's Lua filetype matcher.
-- ftdetect/zerolang.vim covers classic autocmd detection (and Vim); this
-- registration is what vim.filetype.match consults, which scratch buffers
-- (telescope previewers, etc.) use instead of BufRead autocmds.
if vim.filetype and vim.filetype.add then
  vim.filetype.add({ extension = { z = "zerolang" } })
end
