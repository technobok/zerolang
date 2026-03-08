" Vim syntax file
" Language: Zerolang
" Based on the Rouge lexer in editor/rouge/zerolang.rb

if exists("b:current_syntax")
  finish
endif

" Comments: # to end of line
syn match zerolangComment "#.*$" contains=@Spell

" Raw strings: """...""" through """""..."""""
" Order matters — longest delimiter first
syn region zerolangRawString start=/\z\("\{5,}\)/ end=/\z1/ contains=@Spell
syn region zerolangRawString start=/\z\("\{4}\)/ end=/\z1/ contains=@Spell
syn region zerolangRawString start=/\z\("\{3}\)/ end=/\z1/ contains=@Spell

" Interpreted strings
syn region zerolangString start=/"/ skip=/\\./ end=/"/ contains=zerolangEscape,zerolangEscapeError,zerolangInterpolation,@Spell

" Valid escape sequences inside interpreted strings
syn match zerolangEscape /\\[\\bnrt"']/ contained
syn match zerolangEscape /\\x[0-9a-fA-F]\{2}/ contained
syn match zerolangEscape /\\u[0-9a-fA-F]\{4,8}/ contained

" Invalid escape sequences (stray backslash)
syn match zerolangEscapeError /\\[^\\bnrt"'xu{]/ contained
syn match zerolangEscapeError /\\$/ contained

" String interpolation: \{...}
syn region zerolangInterpolation matchgroup=zerolangInterpolationDelim start=/\\{/ end=/}/ contained contains=TOP

" Keywords
syn keyword zerolangKeyword unit record class variant union facet protocol data
syn keyword zerolangKeyword function in out is as
syn keyword zerolangKeyword if when then else
syn keyword zerolangKeyword for while loop with do switch on
syn keyword zerolangKeyword case break continue yield return swap
syn match   zerolangKeyword /=/

" Reserved words (highlighted as errors)
syn keyword zerolangReserved macro goto repeat until flag cell
syn keyword zerolangReserved pragma enum view unsafe

" Built-in / predeclared identifiers
syn keyword zerolangBuiltin null never any _ typedef tag
syn keyword zerolangBuiltin u8 u16 u32 u64 u128
syn keyword zerolangBuiltin i8 i16 i32 i64 i128
syn keyword zerolangBuiltin f8 f16 f32 f64 f128
syn keyword zerolangBuiltin c8 c32 string
syn keyword zerolangBuiltin true false
syn keyword zerolangBuiltin public private
syn keyword zerolangBuiltin this meta error
syn keyword zerolangBuiltin iterator
syn keyword zerolangBuiltin take borrow lock generic

" Labels: word: and :word
" The identifier character class from the Rouge WORD pattern
syn match zerolangLabel /[-!$%&'*+\/<=>?@\\^_|~a-zA-Z0-9]\+:/ contains=zerolangKeyword,zerolangReserved,zerolangBuiltin
syn match zerolangLabel /:[-!$%&'*+\/<=>?@\\^_|~a-zA-Z0-9]\+/

" Illegal characters
syn match zerolangError /[[\],;`]/

" Punctuation (no special highlighting — uses default text color)
syn match zerolangPunctuation /[(){}."]/

" Highlight linking
hi def link zerolangComment    Comment
hi def link zerolangString     String
hi def link zerolangRawString  String
hi def link zerolangEscape     SpecialChar
hi def link zerolangEscapeError Error
hi def link zerolangInterpolation Normal
hi def link zerolangInterpolationDelim Delimiter
hi def link zerolangKeyword    Keyword
hi def link zerolangReserved   Error
hi def link zerolangBuiltin    Type
hi def link zerolangLabel      Label
hi def link zerolangError      Error
hi def link zerolangPunctuation Delimiter

let b:current_syntax = "zerolang"
