-- Zerolang LSP client for Neovim.
--
-- Starts the `zls` language server (built to bin/zls) for `zerolang` buffers via
-- vim.lsp.start -- no plugin dependencies. Opt in from your config:
--
--   require("zerolang.lsp").setup({
--     cmd = { "/path/to/zerolang/bin/zls", "--stdio" },
--     systemDir = "/path/to/zerolang/lib/system",
--   })
--
-- `systemDir` must point at the zerolang standard library (a real directory); it
-- also falls back to $ZEROLANG_SYSTEM. `srcDir` is optional -- when omitted, zls
-- derives it from the workspace root (the directory holding the .z files, or its
-- src/ when the root also has lib/system/), so one config fits both a plain
-- program and the compiler checkout.

local M = {}

local uv = vim.uv or vim.loop

local function is_dir(path)
  local st = uv.fs_stat(path)
  return st ~= nil and st.type == "directory"
end

-- find_root -- the workspace root reported to the server as rootUri: the nearest
-- ancestor holding both src/ and lib/system/ (a compiler-style layout), else one
-- holding .git/, else the opened file's own directory.
local function find_root(fname)
  local dir = vim.fs.dirname(fname)
  local d, prev = dir, nil
  while d and d ~= prev do
    if is_dir(d .. "/src") and is_dir(d .. "/lib/system") then
      return d
    end
    if is_dir(d .. "/.git") then
      return d
    end
    prev, d = d, vim.fs.dirname(d)
  end
  return dir
end

function M.setup(opts)
  opts = opts or {}
  local cmd = opts.cmd or { "zls", "--stdio" }
  local system_dir = opts.systemDir or vim.env.ZEROLANG_SYSTEM

  vim.api.nvim_create_autocmd("FileType", {
    pattern = "zerolang",
    callback = function(args)
      local fname = vim.api.nvim_buf_get_name(args.buf)
      if fname == "" then
        return
      end
      local init_options = {}
      if system_dir and system_dir ~= "" then
        init_options.systemDir = system_dir
      end
      if opts.srcDir and opts.srcDir ~= "" then
        init_options.srcDir = opts.srcDir
      end
      vim.lsp.start({
        name = "zls",
        cmd = cmd,
        root_dir = find_root(fname),
        init_options = init_options,
      }, { bufnr = args.buf })
    end,
  })
end

return M
