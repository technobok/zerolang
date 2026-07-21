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
syn match zerolangKeyword /\<\%(function\|in\|out\|is\|as\|native\)\>/
syn match zerolangKeyword /\<\%(if\|when\|then\|else\)\>/
syn match zerolangKeyword /\<\%(for\|while\|loop\|with\|do\|on\)\>/
syn match zerolangKeyword /\<\%(match\|case\|swap\)\>/
" Operator-class identifiers: runs of non-alphanumeric WORD chars
" (=, ==, !=, <, <=, >, >=, +, -, *, /, &, |, ?, etc.) highlight
" as a single token instead of splitting per character.
syn match zerolangOperator /\<[-!$%&'*+\/<=>?@\\^|~]\+\>/

" Reserved words (highlighted as errors)
syn match zerolangReserved /\<\%(macro\|goto\|repeat\|until\|flag\|cell\)\>/
syn match zerolangReserved /\<\%(pragma\|enum\|view\|unsafe\|switch\)\>/

" Predeclared identifiers: everything defined in lib/system/core.z
" (grep '^name:' lib/system/core.z), split by role, plus the context
" words in the last group. Keep in sync with the `builtins` list in
" docs/style/prism-zerolang.js; a future zls semantic-token layer will
" compute this set from core.z instead of a hand-maintained list.
" Types (core.z type-valued definitions)
syn match zerolangBuiltinType /\<\%(u8\|u16\|u32\|u64\|u128\)\>/
syn match zerolangBuiltinType /\<\%(i8\|i16\|i32\|i64\|i128\)\>/
syn match zerolangBuiltinType /\<\%(f16\|f32\|f64\|f128\|c8\|c32\|bool\)\>/
syn match zerolangBuiltinType /\<\%(String\|StringView\|Text\|StringLike\|Any\)\>/
syn match zerolangBuiltinType /\<\%(Option\|optionval\|OptionView\|Result\|resultval\|convError\|Box\|Iterator\)\>/
syn match zerolangBuiltinType /\<\%(array\|str\|List\|ListView\|ListIter\|Map\|MapKeyIter\|MapItemIter\|MapEntry\|Set\|SetIter\|Bytes\|ByteView\)\>/
syn match zerolangBuiltinType /\<\%(Path\|PathView\|IoError\|Reader\|Writer\|Closer\|Seeker\|seekorigin\|File\|openmode\)\>/
" Constants / literal values
syn match zerolangBuiltinConst /\<\%(null\|never\|true\|false\|_\)\>/
" Predeclared functions, streams, and context words
syn match zerolangBuiltin /\<\%(print\|stringJoin\|error\|panic\|stdin\|stdout\|stderr\)\>/
syn match zerolangBuiltin /\<\%(return\|break\|continue\|yield\)\>/
syn match zerolangBuiltin /\<\%(public\|private\|this\|meta\|typedef\|tag\|iterator\)\>/
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
hi def link zerolangOperator   Operator
hi def link zerolangReserved   Error
hi def link zerolangBuiltinType  Type
hi def link zerolangBuiltinConst Constant
hi def link zerolangBuiltin      Special
hi def link zerolangLabel      Identifier
hi def link zerolangError      Error
hi def link zerolangPunctuation Delimiter

let b:current_syntax = "zerolang"
