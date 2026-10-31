" Vim syntax file
" Language: zdata (`.zd`, docs/zdata.pdoc)
" A data file is written in zerolang's own grammar, so it is highlighted as
" zerolang is.

if exists("b:current_syntax")
  finish
endif

runtime! syntax/zerolang.vim
let b:current_syntax = "zdata"
