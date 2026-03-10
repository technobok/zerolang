" Vim syntax file
" Language: Zerolang
" Based on the Rouge lexer in editor/rouge/zerolang.rb

if exists("b:current_syntax")
  finish
endif

" Set syntax-only keyword chars to match zerolang WORD characters:
"   [-!$%&'*+/<=>?@\^_|~a-zA-Z0-9]
" This makes \< \> word boundaries work correctly for zerolang identifiers.
syn iskeyword @,48-57,33,36-39,42-43,45,47,60-64,92,94-95,124,126

" Zerolang WORD character class for use in patterns (matches syn iskeyword).
" \k cannot be used because it follows buffer iskeyword, not syn iskeyword.
let s:W = '[-!$%&''*+\/<=>?@\\^_|~a-zA-Z0-9]'

" Comments: # to end of line
syn match zerolangComment "#.*$" contains=@Spell

" Raw strings: """...""" through """""..."""""
" Order matters — longest delimiter first
syn region zerolangRawString start=/\z\("\{5,}\)/ end=/\z1/ contains=@Spell
syn region zerolangRawString start=/\z\("\{4}\)/ end=/\z1/ contains=@Spell
syn region zerolangRawString start=/\z\("\{3}\)/ end=/\z1/ contains=@Spell

" Interpreted strings.
" No skip pattern — the contained escape matches (eg. \") prevent the end
" pattern from matching at escaped quotes. Using skip would also block the
" interpolation region from starting at \{.
syn region zerolangString start=/"/ end=/"/ contains=zerolangEscape,zerolangEscapeError,zerolangInterpolation,@Spell

" Valid escape sequences inside interpreted strings
syn match zerolangEscape /\\[\\bnrt"']/ contained
syn match zerolangEscape /\\x[0-9a-fA-F]\{2}/ contained
syn match zerolangEscape /\\u[0-9a-fA-F]\{4,8}/ contained

" Invalid escape sequences (stray backslash)
syn match zerolangEscapeError /\\[^\\bnrt"'xu{]/ contained
syn match zerolangEscapeError /\\$/ contained

" String interpolation: \{...} — code inside is highlighted as top-level
syn region zerolangInterpolation matchgroup=zerolangInterpolationDelim start=/\\{/ end=/}/ contained contains=TOP

" Keywords (use syn match so labels can take priority)
syn match zerolangKeyword /\<\%(unit\|record\|class\|variant\|union\|facet\|protocol\|data\)\>/
syn match zerolangKeyword /\<\%(function\|in\|out\|is\|as\)\>/
syn match zerolangKeyword /\<\%(if\|when\|then\|else\)\>/
syn match zerolangKeyword /\<\%(for\|while\|loop\|with\|do\|on\)\>/
syn match zerolangKeyword /\<\%(match\|case\|break\|continue\|yield\|return\|swap\)\>/
syn match zerolangKeyword /\<=/

" Reserved words (highlighted as errors)
syn match zerolangReserved /\<\%(macro\|goto\|repeat\|until\|flag\|cell\)\>/
syn match zerolangReserved /\<\%(pragma\|enum\|view\|unsafe\|switch\)\>/

" Built-in / predeclared identifiers
syn match zerolangBuiltin /\<\%(null\|never\|any\|_\|typedef\|tag\)\>/
syn match zerolangBuiltin /\<\%(u8\|u16\|u32\|u64\|u128\)\>/
syn match zerolangBuiltin /\<\%(i8\|i16\|i32\|i64\|i128\)\>/
syn match zerolangBuiltin /\<\%(f8\|f16\|f32\|f64\|f128\)\>/
syn match zerolangBuiltin /\<\%(c8\|c32\|string\)\>/
syn match zerolangBuiltin /\<\%(true\|false\)\>/
syn match zerolangBuiltin /\<\%(public\|private\)\>/
syn match zerolangBuiltin /\<\%(this\|meta\|error\)\>/
syn match zerolangBuiltin /\<iterator\>/
syn match zerolangBuiltin /\<\%(take\|borrow\|lock\|generic\)\>/

" Labels: word: and :word (defined after keywords — longer match wins)
exe 'syn match zerolangLabel /' . s:W . '\+:/'
exe 'syn match zerolangLabel /:' . s:W . '\+/'

" Illegal characters
syn match zerolangError /[[\],;`]/

" Punctuation (no special highlighting — uses default text color)
" Note: " is NOT included — it is handled by string regions
syn match zerolangPunctuation /[(){}.]/

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
hi def link zerolangLabel      Identifier
hi def link zerolangError      Error
hi def link zerolangPunctuation Delimiter

let b:current_syntax = "zerolang"
