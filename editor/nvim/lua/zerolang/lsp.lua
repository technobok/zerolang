-- Zerolang LSP client for Neovim.
--
-- Starts the `zls` language server (built to bin/zls) for `zerolang` buffers via
-- vim.lsp.start -- no plugin dependencies. Put this checkout's editor/nvim on
-- your runtimepath, then opt in with a zero-config call:
--
--   require("zerolang.lsp").setup()
--
-- setup() locates `bin/zls` and `lib/system` relative to this file's checkout,
-- so no paths are needed. Override any of them if your layout differs:
--
--   require("zerolang.lsp").setup({
--     cmd = { "/path/to/zls", "--stdio" },
--     systemDir = "/path/to/lib/system",
--     srcDir = "/path/to/program",   -- optional; else auto-detected
--   })
--
-- `systemDir` must point at the zerolang standard library (a real directory); it
-- also falls back to $ZEROLANG_SYSTEM. `srcDir` is optional -- when omitted, zls
-- derives it from the workspace root (the directory holding the .z files, or its
-- src/ when the root also has lib/system/), so one config fits both a plain
-- program and the compiler checkout.

local M = {}

-- This file is <checkout>/editor/nvim/lua/zerolang/lsp.lua, so five dirname
-- steps up from it is the checkout that holds bin/zls and lib/system/.
local here = debug.getinfo(1, "S").source:sub(2)
local checkout = vim.fn.fnamemodify(here, ":h:h:h:h:h")

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
  local cmd = opts.cmd or { checkout .. "/bin/zls", "--stdio" }
  local system_dir = opts.systemDir or vim.env.ZEROLANG_SYSTEM or (checkout .. "/lib/system")

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
