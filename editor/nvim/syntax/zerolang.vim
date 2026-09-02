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
syn match zerolangKeyword /\<\%(unit\|record\|class\|variant\|union\|facet\|protocol\|data\|generator\)\>/
syn match zerolangKeyword /\<\%(function\|in\|out\|outx\|is\|as\|native\)\>/
syn match zerolangKeyword /\<\%(if\|when\|then\|else\)\>/
syn match zerolangKeyword /\<\%(for\|while\|loop\|with\|do\|on\)\>/
syn match zerolangKeyword /\<\%(match\|case\|swap\)\>/
" Operator-class identifiers: runs of non-alphanumeric WORD chars
" (=, ==, !=, <, <=, >, >=, +, -, *, /, &, |, ?, etc.) highlight
" as a single token instead of splitting per character.
syn match zerolangOperator /\<[-!$%&'*+\/<=>?@\\^|~]\+\>/
" ...except a lone `=`, which is the assignment KEYWORD (zlexer.z lexes it as
" equalsTok and the spec lists it under Keywords). Defined after the operator
" rule so it wins at the same position; `==` has no word boundary before its
" second character, so a run is never broken up.
syn match zerolangKeyword /\<=\>/

" Reserved words (highlighted as errors). Exactly islookupReserved in
" lib/system/zlexer.z. `view` is NOT here -- it is an ownership marker and a
" predeclared identifier (below); it was listed in both, and reserved wins.
syn match zerolangReserved /\<\%(macro\|goto\|repeat\|until\|flag\|cell\)\>/
syn match zerolangReserved /\<\%(pragma\|enum\|unsafe\|switch\)\>/

" Predeclared identifiers: everything defined in lib/system/core.z
" (grep '^name:' lib/system/core.z), split by role, plus the context
" words in the last group. Keep in sync with the `builtins` list in
" docs/style/prism-zerolang.js; a future zls semantic-token layer will
" compute this set from core.z instead of a hand-maintained list.
" Types (core.z type-valued definitions)
syn match zerolangBuiltinType /\<\%(u8\|u16\|u32\|u64\|u128\)\>/
syn match zerolangBuiltinType /\<\%(i8\|i16\|i32\|i64\|i128\)\>/
syn match zerolangBuiltinType /\<\%(f16\|f32\|f64\|f128\|c8\|c32\|bool\)\>/
syn match zerolangBuiltinType /\<\%(String\|StringView\|Text\|StringLike\|Any\|AnyRef\|anyval\|RefHashable\|valhashable\)\>/
syn match zerolangBuiltinType /\<\%(Option\|optionval\|OptionView\|Result\|resultval\|converror\|Box\|Iterator\)\>/
syn match zerolangBuiltinType /\<\%(array\|str\|List\|ListRef\|ListVal\|ListView\|ListViewVal\|ListIter\|ListIterVal\|Set\|SetRef\|SetVal\|SetIter\|SetIterVal\|Bytes\|ByteView\)\>/
syn match zerolangBuiltinType /\<\%(Map\|MapRR\|MapRV\|MapVR\|MapVV\|MapKeyIter\|MapItemIter\|MapEntry\|MapKeyIterRV\|MapKeyIterVR\|MapKeyIterVV\|MapItemIterRV\|MapItemIterVR\|MapItemIterVV\|MapEntryRV\|MapEntryVR\|MapEntryVV\)\>/
syn match zerolangBuiltinType /\<\%(Path\|PathView\|IoError\|Reader\|Writer\|Closer\|Seeker\|seekorigin\|File\|openmode\)\>/
syn match zerolangBuiltinType /\<\%(IdMapR\|IdMapV\|IdMapEntryR\|IdMapEntryV\|IdMapItemIterR\|IdMapItemIterV\|IdSet\|IdSetIter\)\>/
syn match zerolangBuiltinType /\<\%(CpIter\|LinesIter\|Splitter\|TextReader\|intliteral\|floatliteral\|idkey\|parseerror\)\>/
" Constants / literal values
syn match zerolangBuiltinConst /\<\%(null\|never\|true\|false\|_\)\>/
" Predeclared functions, streams, and context words
syn match zerolangBuiltin /\<\%(print\|stringJoin\|error\|panic\|stdin\|stdout\|stderr\)\>/
syn match zerolangBuiltin /\<\%(return\|break\|continue\|yield\)\>/
syn match zerolangBuiltin /\<\%(public\|private\|this\|meta\|typedef\|tag\|iterator\)\>/
syn match zerolangBuiltin /\<\%(take\|borrow\|view\|hold\|copy\|drop\|generic\)\>/

" Labels: word: and :word (defined after keywords — longer match wins)
exe 'syn match zerolangLabel /' . s:W . '\+:/'
exe 'syn match zerolangLabel /:' . s:W . '\+/'

" Illegal characters
syn match zerolangError /[[\],;`]/

" Punctuation (no special highlighting — uses default text color)
" Note: " is NOT included — it is handled by string regions
syn match zerolangPunctuation /[(){}.]/

" Highlight linking.
"
" The groups above are the definitions -- they draw distinctions the web
" highlighter does not, and a colourscheme may restyle any of them. The COLOURS
" come from docs/style/zerolang.css, so a snippet reads the same in the
" documentation and in the editor: keywords purple, predeclared identifiers
" blue, strings green, labels red, errors black on red. The two palettes are
" One Light and One Dark, chosen by &background.
"
" Set g:zerolang_no_colors to skip these and inherit the colourscheme through
" the standard groups instead.
if get(g:, 'zerolang_no_colors', 0)
  hi def link zerolangKeyword      Keyword
  hi def link zerolangOperator     Operator
  hi def link zerolangBuiltinType  Type
  hi def link zerolangBuiltinConst Constant
  hi def link zerolangBuiltin      Special
  hi def link zerolangLabel        Identifier
  hi def link zerolangString       String
  hi def link zerolangComment      Comment
elseif &background ==# 'dark'
  hi def zerolangKeyword      guifg=#C678DD ctermfg=176
  hi def zerolangOperator     guifg=#546d78 ctermfg=66
  hi def zerolangBuiltinType  guifg=#61AFEF ctermfg=75
  hi def zerolangBuiltinConst guifg=#61AFEF ctermfg=75
  hi def zerolangBuiltin      guifg=#61AFEF ctermfg=75
  hi def zerolangLabel        guifg=#E06C75 ctermfg=168
  hi def zerolangString       guifg=#98C379 ctermfg=114
  hi def zerolangComment      guifg=#5C6370 ctermfg=59 gui=italic cterm=italic
else
  hi def zerolangKeyword      guifg=#A626A4 ctermfg=127
  hi def zerolangOperator     guifg=#546d78 ctermfg=66
  hi def zerolangBuiltinType  guifg=#4078F2 ctermfg=33
  hi def zerolangBuiltinConst guifg=#4078F2 ctermfg=33
  hi def zerolangBuiltin      guifg=#4078F2 ctermfg=33
  hi def zerolangLabel        guifg=#E45649 ctermfg=167
  hi def zerolangString       guifg=#50A14F ctermfg=71
  hi def zerolangComment      guifg=#A0A1A7 ctermfg=145 gui=italic cterm=italic
endif

" The web renders an illegal character and a reserved word identically: black
" on #f44747, the loudest style in the sheet.
hi def zerolangReserved   guifg=#000000 guibg=#f44747 ctermfg=0 ctermbg=203
hi def zerolangError      guifg=#000000 guibg=#f44747 ctermfg=0 ctermbg=203
hi def zerolangEscapeError guifg=#000000 guibg=#f44747 ctermfg=0 ctermbg=203

hi def link zerolangRawString  zerolangString
hi def link zerolangEscape     SpecialChar
hi def link zerolangInterpolation Normal
hi def link zerolangInterpolationDelim Delimiter
hi def link zerolangPunctuation Delimiter

" Treesitter-first colourschemes style the modern captures rather than the
" legacy groups, so name both.
hi def link @keyword.zerolang          zerolangKeyword
hi def link @type.builtin.zerolang     zerolangBuiltinType
hi def link @constant.builtin.zerolang zerolangBuiltinConst
hi def link @function.builtin.zerolang zerolangBuiltin
hi def link @operator.zerolang         zerolangOperator
hi def link @string.zerolang           zerolangString
hi def link @comment.zerolang          zerolangComment

let b:current_syntax = "zerolang"
