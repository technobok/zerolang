# Zerolang Naming Audit vs Style Guide (regenerated)

Generated against doc/styleguide.pdoc as of 2026-04-27.
Scope: lib/system/*.z and examples/*.z.
Decisions: predefined IDs renamed; get-prefix dropped; x86_64 carved out.
Lists only names that would change.

## lib/system

### cli.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `cli.z::flagdef` | class | `flagdef` | `FlagDef` |  |
| `cli.z::optiondef` | class | `optiondef` | `OptionDef` |  |
| `cli.z::positionaldef` | class | `positionaldef` | `PositionalDef` |  |
| `cli.z::clierror` | union | `clierror` | `CliError` |  |
| `cli.z::spec` | class | `spec` | `Spec` |  |
| `cli.z::parsed` | class | `parsed` | `Parsed` |  |
| `cli.z::parsed.has_flag` | method | `has_flag` | `hasFlag` | predicate: convert underscores, keep 'has' |
| `cli.z::parsed.get_option` | method | `get_option` | `option` | getter: drop 'get_' prefix per §3.5 |
| `cli.z::parsed.get_positional` | method | `get_positional` | `positional` | getter: drop 'get_' prefix per §3.5 |
| `cli.z::add_flag` | function | `add_flag` | `addFlag` |  |
| `cli.z::add_option` | function | `add_option` | `addOption` |  |
| `cli.z::add_positional` | function | `add_positional` | `addPositional` |  |
| `cli.z::help_text` | function | `help_text` | `helpText` |  |

### collections.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `collections.z::list` | class | `list` | `List` |  |
| `collections.z::listiter` | class | `listiter` | `ListIter` |  |
| `collections.z::listview` | class | `listview` | `ListView` |  |
| `collections.z::map` | class | `map` | `Map` |  |
| `collections.z::mapkeyiter` | class | `mapkeyiter` | `MapKeyIter` |  |
| `collections.z::mapitemiter` | class | `mapitemiter` | `MapItemIter` |  |
| `collections.z::mapentry` | class | `mapentry` | `MapEntry` |  |
| `collections.z::string_join` | function | `string_join` | `stringJoin` |  |

### io.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `io.z::ioerror` | union | `ioerror` | `IoError` |  |
| `io.z::read_text` | function | `read_text` | `readText` |  |
| `io.z::write_text` | function | `write_text` | `writeText` |  |
| `io.z::append_text` | function | `append_text` | `appendText` |  |
| `io.z::list_dir` | function | `list_dir` | `listDir` |  |
| `io.z::reader` | protocol | `reader` | `Reader` |  |
| `io.z::writer` | protocol | `writer` | `Writer` |  |
| `io.z::closer` | protocol | `closer` | `Closer` |  |
| `io.z::seeker` | protocol | `seeker` | `Seeker` |  |
| `io.z::file` | class | `file` | `File` |  |
| `io.z::bufwriter` | class | `bufwriter` | `BufWriter` |  |
| `io.z::bufreader` | class | `bufreader` | `BufReader` |  |
| `io.z::textwriter` | class | `textwriter` | `TextWriter` |  |
| `io.z::textreader` | class | `textreader` | `TextReader` |  |

### os.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `os.z::get_env` | function | `get_env` | `env` | getter: drop 'get_' prefix per §3.5 |
| `os.z::set_env` | function | `set_env` | `setEnv` | setter: keep 'set' prefix per §3.5 |
| `os.z::unset_env` | function | `unset_env` | `unsetEnv` |  |
| `os.z::env_names` | function | `env_names` | `envNames` |  |
| `os.z::set_cwd` | function | `set_cwd` | `setCwd` | setter: keep 'set' prefix per §3.5 |
| `os.z::user_name` | function | `user_name` | `userName` |  |
| `os.z::home_dir` | function | `home_dir` | `homeDir` |  |

### system.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `system.z::string` | class | `string` | `String` |  |
| `system.z::stringview` | class | `stringview` | `StringView` |  |
| `system.z::stringview.is_empty` | method | `is_empty` | `isEmpty` | predicate: convert underscores, keep 'is' |
| `system.z::stringview.is_ascii` | method | `is_ascii` | `isAscii` | predicate: convert underscores, keep 'is' |
| `system.z::stringview.starts_with` | method | `starts_with` | `startsWith` | predicate: convert underscores, keep 'starts' |
| `system.z::stringview.ends_with` | method | `ends_with` | `endsWith` | predicate: convert underscores, keep 'ends' |
| `system.z::stringview.index_of` | method | `index_of` | `indexOf` |  |
| `system.z::stringview.last_index_of` | method | `last_index_of` | `lastIndexOf` |  |
| `system.z::stringview.byte_at` | method | `byte_at` | `byteAt` |  |
| `system.z::stringview.trim_start` | method | `trim_start` | `trimStart` |  |
| `system.z::stringview.trim_end` | method | `trim_end` | `trimEnd` |  |
| `system.z::stringview.strip_prefix` | method | `strip_prefix` | `stripPrefix` |  |
| `system.z::stringview.strip_suffix` | method | `strip_suffix` | `stripSuffix` |  |
| `system.z::stringview.split_once` | method | `split_once` | `splitOnce` |  |
| `system.z::stringview.to_lower_ascii` | method | `to_lower_ascii` | `toLowerAscii` |  |
| `system.z::stringview.to_upper_ascii` | method | `to_upper_ascii` | `toUpperAscii` |  |
| `system.z::stringview.replace_first` | method | `replace_first` | `replaceFirst` |  |
| `system.z::stringview.parse_i64` | method | `parse_i64` | `parseI64` |  |
| `system.z::stringview.parse_u64` | method | `parse_u64` | `parseU64` |  |
| `system.z::stringview.parse_f64` | method | `parse_f64` | `parseF64` |  |
| `system.z::cpiter` | class | `cpiter` | `CpIter` |  |
| `system.z::splitter` | class | `splitter` | `Splitter` |  |
| `system.z::linesiter` | class | `linesiter` | `LinesIter` |  |
| `system.z::text` | protocol | `text` | `Text` |  |
| `system.z::stringlike` | union | `stringlike` | `StringLike` |  |
| `system.z::any` | union | `any` | `Any` |  |
| `system.z::option` | union | `option` | `Option` |  |
| `system.z::optionview` | union | `optionview` | `OptionView` |  |
| `system.z::result` | union | `result` | `Result` |  |
| `system.z::bytes` | class | `bytes` | `Bytes` |  |
| `system.z::byteview` | class | `byteview` | `ByteView` |  |
| `system.z::path` | class | `path` | `Path` |  |
| `system.z::pathview` | class | `pathview` | `PathView` |  |
| `system.z::box` | class | `box` | `Box` |  |

## examples

### atomic_call_temps.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `atomic_call_temps.z::reader` | protocol | `reader` | `Reader` |  |
| `atomic_call_temps.z::myfile` | class | `myfile` | `MyFile` |  |
| `atomic_call_temps.z::use_reader` | function | `use_reader` | `useReader` |  |

### autoproject.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `autoproject.z::greeter` | protocol | `greeter` | `Greeter` |  |
| `autoproject.z::cat` | class | `cat` | `Cat` |  |
| `autoproject.z::use_greeter` | function | `use_greeter` | `useGreeter` |  |

### borrowed_record.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `borrowed_record.z::container` | class | `container` | `Container` |  |
| `borrowed_record.z::cview` | class | `cview` | `CView` |  |

### chained_method_calls.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `chained_method_calls.z::make_val` | function | `make_val` | `makeVal` |  |

### class_text_protocol.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `class_text_protocol.z::mylabel` | class | `mylabel` | `MyLabel` |  |

### classes.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `classes.z::counter` | class | `counter` | `Counter` |  |
| `classes.z::make_counter` | function | `make_counter` | `makeCounter` |  |
| `classes.z::named` | class | `named` | `Named` |  |

### compileerror.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `compileerror.z::check_mode` | function | `check_mode` | `checkMode` |  |
| `compileerror.z::check_level` | function | `check_level` | `checkLevel` |  |

### constructors.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `constructors.z::counter` | class | `counter` | `Counter` |  |

### create_null.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `create_null.z::secret` | class | `secret` | `Secret` |  |

### facets.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `facets.z::use_facet` | function | `use_facet` | `useFacet` |  |

### field_reassign.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `field_reassign.z::holder` | class | `holder` | `Holder` |  |

### forloop.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `forloop.z::counter` | class | `counter` | `Counter` |  |
| `forloop.z::greetings` | class | `greetings` | `Greetings` |  |

### genericfunctions.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `genericfunctions.z::id_val` | function | `id_val` | `idVal` |  |

### generics.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `generics.z::myoption` | union | `myoption` | `MyOption` |  |
| `generics.z::mybox` | class | `mybox` | `MyBox` |  |
| `generics.z::provider` | protocol | `provider` | `Provider` |  |

### io_protocol_rw.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `io_protocol_rw.z::write_line` | function | `write_line` | `writeLine` |  |
| `io_protocol_rw.z::read_line` | function | `read_line` | `readLine` |  |

### iterator.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `iterator.z::provider` | protocol | `provider` | `Provider` |  |
| `iterator.z::adder` | class | `adder` | `Adder` |  |
| `iterator.z::multiplier` | class | `multiplier` | `Multiplier` |  |
| `iterator.z::use_provider` | function | `use_provider` | `useProvider` |  |

### linkedlist.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `linkedlist.z::box` | class | `box` | `Box` |  |
| `linkedlist.z::entry` | class | `entry` | `Entry` |  |
| `linkedlist.z::consume_entry` | function | `consume_entry` | `consumeEntry` |  |

### listiter.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `listiter.z::bag` | class | `bag` | `Bag` |  |
| `listiter.z::bagiter` | class | `bagiter` | `BagIter` |  |

### owned_protocol.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `owned_protocol.z::reader` | protocol | `reader` | `Reader` |  |
| `owned_protocol.z::myfile` | class | `myfile` | `MyFile` |  |
| `owned_protocol.z::make_reader` | function | `make_reader` | `makeReader` |  |

### ownership.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `ownership.z::box` | class | `box` | `Box` |  |
| `ownership.z::peek_label` | function | `peek_label` | `peekLabel` |  |
| `ownership.z::make_box` | function | `make_box` | `makeBox` |  |

### panic.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `panic.z::check_positive` | function | `check_positive` | `checkPositive` |  |

### path_locks.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `path_locks.z::person` | class | `person` | `Person` |  |

### protocols.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `protocols.z::reader` | protocol | `reader` | `Reader` |  |
| `protocols.z::myfile` | class | `myfile` | `MyFile` |  |
| `protocols.z::use_reader` | function | `use_reader` | `useReader` |  |

### result.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `result.z::int_result` | union | `int_result` | `IntResult` |  |
| `result.z::show_int` | function | `show_int` | `showInt` |  |

### specs.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `specs.z::processor` | class | `processor` | `Processor` |  |

### str.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `str.z::show_length` | function | `show_length` | `showLength` |  |

### text_protocol.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `text_protocol.z::mylabel` | class | `mylabel` | `MyLabel` |  |

### typedefs.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `typedefs.z::add_one` | function | `add_one` | `addOne` |  |

### unions.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `unions.z::result` | union | `result` | `Result` |  |
| `unions.z::typed` | union | `typed` | `Typed` |  |
| `unions.z::priority` | union | `priority` | `Priority` |  |

### visibility.z

| Path | Kind | Current | Proposed | Notes |
|------|------|---------|----------|-------|
| `visibility.z::adder` | class | `adder` | `Adder` |  |

## Summary

- Total changes: 129
- By category:
  - Reftypes (class/union/protocol): ~75
  - Functions: ~35
  - Methods: ~19
- Rationale applied:
  - **Reftypes**: All class/union/protocol names get PascalCase per §3.1
  - **Predefined IDs**: string → String, option → Option, result → Result, any → Any, text → Text, stringview → StringView, all renamed per policy decision
  - **Functions**: Underscore-separated names → lowerCamelCase. Getters (get_*) drop the prefix per §3.5 (e.g., get_env → env, get_option → option, get_positional → positional)
  - **Setters**: Keep "set" prefix (set_env → setEnv, set_cwd → setCwd) per §3.5
  - **Predicates**: Convert underscores but keep prefixes (is_empty → isEmpty, starts_with → startsWith, ends_with → endsWith, has_flag → hasFlag) per §3.5
  - **Methods**: Same rules as functions
  - **Excluded**: x86_64 (carved out), single-word identifiers already compliant, operators
