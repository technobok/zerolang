/**
 * Prism syntax highlighting definition for Zerolang.
 *
 * Based on the Rouge lexer in editor/rouge/zerolang.rb.
 *
 * Token mapping (Prism ← Rouge):
 *   comment     ← Comment::Single
 *   keyword     ← Keyword
 *   builtin     ← Name::Builtin
 *   string      ← Literal::String / Literal::String::Backtick
 *   escape      ← Literal::String::Escape
 *   property    ← Name::Label  (labels: word: and :word)
 *   namespace   ← path component before dot
 *   variable    ← Name::Variable
 *   punctuation ← Punctuation
 *   error       ← Error (reserved words, illegal chars)
 */
(function (Prism) {

    // Identifier character class matching the Rouge WORD pattern:
    //   [-!$%&'*+\/<=>?@\\^_|~a-zA-Z0-9]+
    var ID_CHAR = "[-!$%&'*+\\/<=>?@\\\\^_|~a-zA-Z0-9]";
    var WORD    = ID_CHAR + '+';

    var keywords = [
        'unit', 'record', 'class', 'variant', 'union', 'facet', 'protocol', 'data',
        'function', 'in', 'out', 'is', 'as',
        'if', 'when', 'then', 'else',
        'for', 'while', 'loop', 'with', 'do', 'switch', 'on',
        'match', 'case', 'break', 'continue', 'yield', 'return', 'swap'
    ];

    var reserved = [
        'macro', 'goto', 'repeat', 'until', 'flag', 'cell',
        'pragma', 'enum', 'view', 'unsafe'
    ];

    var builtins = [
        'null', 'never', 'any', '_', 'typedef', 'tag',
        'u8', 'u16', 'u32', 'u64', 'u128',
        'i8', 'i16', 'i32', 'i64', 'i128',
        'f8', 'f16', 'f32', 'f64', 'f128',
        'c8', 'c32', 'string',
        'true', 'false',
        'public', 'private',
        'this', 'meta', 'error',
        'iterator',
        'take', 'borrow', 'lock', 'generic'
    ];

    // Build a regex that matches a full WORD token and classifies it.
    // We use a callback via Prism.hooks to reclassify generic word tokens.

    Prism.languages.zerolang = {
        'comment': {
            pattern: /#.*/,
            greedy: true
        },

        // Raw strings: 5, 4, 3 opening/closing quotes (order matters)
        'string': [
            { pattern: /"{5}[\s\S]*?"{5}/, greedy: true },
            { pattern: /"{4}[\s\S]*?"{4}/, greedy: true },
            { pattern: /"{3}[\s\S]*?"{3}/, greedy: true },
            // Interpreted strings with escape sequences and interpolation
            {
                pattern: /"(?:[^"\\]|\\.)*"/,
                greedy: true,
                inside: {
                    'interpolation': {
                        pattern: /\\\{[^}]*\}/,
                        inside: {
                            'interpolation-punctuation': {
                                pattern: /^\\\{|\}$/,
                                alias: 'punctuation'
                            },
                            rest: null  // filled below
                        }
                    },
                    'escape': /\\(?:[\\bnrt"']|x[a-fA-F0-9]{2}|u[a-fA-F0-9]{4,8})/,
                    'error': /\\./,
                    'string': /[\s\S]+/
                }
            }
        ],

        // Labels: word: (post-colon) and :word (pre-colon)
        'property': [
            {
                // word: (label definition)
                pattern: new RegExp(WORD + ':'),
                greedy: true
            },
            {
                // :word (label value / shorthand)
                pattern: new RegExp(':' + WORD),
                greedy: true
            }
        ],

        // Path notation: word.word.word — highlight the leading segments
        'namespace': {
            pattern: new RegExp(WORD + '(?=\\.)'),
            greedy: true,
            inside: {
                'keyword':  { pattern: new RegExp('^(?:' + keywords.join('|') + ')$') },
                'error':    { pattern: new RegExp('^(?:' + reserved.join('|') + ')$') },
                'builtin':  { pattern: new RegExp('^(?:' + builtins.join('|').replace(/[|]/g, '|').replace('_', '\\_') + ')$') },
                'variable': /[\s\S]+/
            }
        },

        // Illegal characters (from Rouge)
        'error': /[[\],;`]/,

        'punctuation': /[(){}."]/,

        // The = keyword (must come after punctuation to avoid conflicts)
        'keyword': {
            pattern: /=/
        },

        // General word tokens — classified by a hook below
        'word': {
            pattern: new RegExp(WORD),
            greedy: true
        }
    };

    // Set up interpolation recursion
    var interpInside = Prism.languages.zerolang['string'][3].inside['interpolation'].inside;
    interpInside.rest = Prism.languages.zerolang;

    // Reclassify generic 'word' tokens into keyword/reserved/builtin/variable
    var kwSet       = Object.create(null);
    var reservedSet = Object.create(null);
    var builtinSet  = Object.create(null);

    keywords.forEach(function (w) { kwSet[w] = true; });
    reserved.forEach(function (w) { reservedSet[w] = true; });
    builtins.forEach(function (w) { builtinSet[w] = true; });

    Prism.hooks.add('after-tokenize', function (env) {
        if (env.language !== 'zerolang') return;

        function walk(tokens) {
            for (var i = 0; i < tokens.length; i++) {
                var token = tokens[i];
                if (typeof token === 'string') continue;

                if (token.type === 'word') {
                    var content = typeof token.content === 'string'
                        ? token.content
                        : flattenContent(token.content);

                    if (kwSet[content]) {
                        token.type = 'keyword';
                    } else if (reservedSet[content]) {
                        token.type = 'error';
                    } else if (builtinSet[content]) {
                        token.type = 'builtin';
                    } else {
                        token.type = 'variable';
                    }
                } else if (Array.isArray(token.content)) {
                    walk(token.content);
                }
            }
        }

        walk(env.tokens);
    });

    function flattenContent(content) {
        if (typeof content === 'string') return content;
        if (Array.isArray(content)) return content.map(flattenContent).join('');
        if (content && typeof content.content !== 'undefined') return flattenContent(content.content);
        return '';
    }

}(Prism));
