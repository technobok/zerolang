static z_@Result_Parsed_CliError@_t z_cli_parse(z_@Spec@_t* spec, z_@List_String@_t args);
static z_@Result_Parsed_CliError@_t z_cli_parse(z_@Spec@_t* spec, z_@List_String@_t args) {
    z_@Parsed@_t* parsed = (z_@Parsed@_t*)z_xmalloc(sizeof(z_@Parsed@_t));
    parsed->flagSet = z_@List_String@_create(0);
    parsed->optionValues = z_@Map_String_String@_create(0);
    parsed->positionalValues = z_@Map_String_String@_create(0);
    parsed->extraArgs = z_@List_String@_create(0);
    uint64_t next_pos = 0;
    uint64_t i = 1;  /* skip argv[0] */
    while (i < args.length) {
        z_@String@_t* a = &args.data[i];
        /* `--` alone terminates option parsing */
        if (a->size == 2 && a->data[0] == '-' && a->data[1] == '-') {
            for (uint64_t j = i + 1; j < args.length; j++) {
                z_@List_String@_append(
                    &parsed->extraArgs,
                    z_@String@_new(args.data[j].data)
                );
            }
            break;
        }
        /* long form `--name[=value]` */
        if (a->size >= 3 && a->data[0] == '-' && a->data[1] == '-') {
            /* find `=` within the arg */
            const char* eq = (const char*)memchr(a->data, '=', a->size);
            uint64_t name_len = eq ? (uint64_t)(eq - a->data) : a->size;
            int64_t fi = z_cli_find_flag_long(&spec->flags, a->data, name_len);
            if (fi >= 0) {
                if (eq) {
                    z_@Parsed@_destroy(parsed); free(parsed); z_@List_String@_destroy(&args);
                    return z_cli_wrap_err(
                        z_cli_err(Z_CLIERROR_TAG_UNEXPECTEDARG,
                                  a->data, a->size));
                }
                z_@List_String@_append(
                    &parsed->flagSet,
                    z_@String@_new(spec->flags.data[fi].name.data)
                );
                i++;
                continue;
            }
            int64_t oi = z_cli_findOption_long(
                &spec->options, a->data, name_len);
            if (oi >= 0) {
                z_@String@_t key = z_@String@_new(spec->options.data[oi].name.data);
                z_@String@_t val = {0};
                if (eq) {
                    uint64_t vlen = a->size - (name_len + 1);
                    val = z_@String@_create(vlen);
                    z_@String@_append(&val, eq + 1, vlen);
                } else {
                    if (i + 1 >= args.length) {
                        z_@String@_free(&key);
                        z_@Parsed@_destroy(parsed); free(parsed); z_@List_String@_destroy(&args);
                        return z_cli_wrap_err(
                            z_cli_err(Z_CLIERROR_TAG_MISSINGVALUE,
                                      a->data, a->size));
                    }
                    val = z_@String@_new(args.data[i + 1].data);
                    i++;
                }
                z_@Map_String_String@_set(
                    parsed->optionValues, key, val);
                i++;
                continue;
            }
            z_@Parsed@_destroy(parsed); free(parsed); z_@List_String@_destroy(&args);
            return z_cli_wrap_err(
                z_cli_err(Z_CLIERROR_TAG_UNKNOWNFLAG,
                          a->data, a->size));
        }
        /* short form `-x[yz...]` */
        if (a->size >= 2 && a->data[0] == '-') {
            /* full-token match first (e.g. -v, -o with separate value) */
            int64_t fi = z_cli_find_flag_short(&spec->flags, a->data, a->size);
            if (fi >= 0) {
                z_@List_String@_append(
                    &parsed->flagSet,
                    z_@String@_new(spec->flags.data[fi].name.data)
                );
                i++;
                continue;
            }
            int64_t oi = z_cli_findOption_short(
                &spec->options, a->data, a->size);
            if (oi >= 0) {
                if (i + 1 >= args.length) {
                    z_@Parsed@_destroy(parsed); free(parsed); z_@List_String@_destroy(&args);
                    return z_cli_wrap_err(
                        z_cli_err(Z_CLIERROR_TAG_MISSINGVALUE,
                                  a->data, a->size));
                }
                z_@String@_t key = z_@String@_new(spec->options.data[oi].name.data);
                z_@String@_t val = z_@String@_new(args.data[i + 1].data);
                z_@Map_String_String@_set(
                    parsed->optionValues, key, val);
                i += 2;
                continue;
            }
            /* bundled short flags: every char after `-` must be
               a short flag. A short option terminates and takes
               the rest as its value. */
            if (a->size > 2) {
                bool ok = true;
                for (uint64_t k = 1; k < a->size && ok; k++) {
                    char buf[2] = { '-', a->data[k] };
                    int64_t bfi = z_cli_find_flag_short(&spec->flags, buf, 2);
                    if (bfi >= 0) {
                        z_@List_String@_append(
                            &parsed->flagSet,
                            z_@String@_new(spec->flags.data[bfi].name.data)
                        );
                        continue;
                    }
                    int64_t boi = z_cli_findOption_short(
                        &spec->options, buf, 2);
                    if (boi >= 0) {
                        uint64_t vlen = a->size - (k + 1);
                        z_@String@_t key = z_@String@_new(
                            spec->options.data[boi].name.data);
                        z_@String@_t val = z_@String@_create(vlen);
                        if (vlen > 0) z_@String@_append(&val, a->data + k + 1, vlen);
                        z_@Map_String_String@_set(
                            parsed->optionValues, key, val);
                        /* consume rest of arg as value */
                        break;
                    }
                    ok = false;
                }
                if (!ok) {
                    z_@Parsed@_destroy(parsed); free(parsed); z_@List_String@_destroy(&args);
                    return z_cli_wrap_err(
                        z_cli_err(Z_CLIERROR_TAG_UNKNOWNFLAG,
                                  a->data, a->size));
                }
                i++;
                continue;
            }
            /* `-` alone or unrecognised short: fall through to positional */
        }
        /* positional */
        if (next_pos < spec->positionals.length) {
            z_@String@_t key = z_@String@_new(
                spec->positionals.data[next_pos].name.data);
            z_@String@_t val = z_@String@_new(a->data);
            z_@Map_String_String@_set(
                parsed->positionalValues, key, val);
            next_pos++;
        } else {
            z_@List_String@_append(
                &parsed->extraArgs, z_@String@_new(a->data));
        }
        i++;
    }
    /* parse loop finished reading — args copies have been
       materialised via z_@String@_new wherever needed. Free the
       incoming list now so every exit path (ok or err) is
       leak-free. */
    z_@List_String@_destroy(&args);
    /* required checks: map.get returns a borrowed OptionView into the map's
       storage; read the tag only -- the map owns the value, so no free. */
    for (uint64_t k = 0; k < spec->options.length; k++) {
        if (!spec->options.data[k].required) continue;
        z_@OptionView_String@_t v = z_@Map_String_String@_get(
            parsed->optionValues, &spec->options.data[k].name);
        bool missing = (v.tag == Z_OPTIONVIEW_STRING_TAG_NONE);
        if (missing) {
            z_@Parsed@_destroy(parsed); free(parsed);
            return z_cli_wrap_err(
                z_cli_err(Z_CLIERROR_TAG_MISSINGREQUIRED,
                          spec->options.data[k].name.data,
                          spec->options.data[k].name.size));
        }
    }
    for (uint64_t k = 0; k < spec->positionals.length; k++) {
        if (!spec->positionals.data[k].required) continue;
        z_@OptionView_String@_t v = z_@Map_String_String@_get(
            parsed->positionalValues, &spec->positionals.data[k].name);
        bool missing = (v.tag == Z_OPTIONVIEW_STRING_TAG_NONE);
        if (missing) {
            z_@Parsed@_destroy(parsed); free(parsed);
            return z_cli_wrap_err(
                z_cli_err(Z_CLIERROR_TAG_MISSINGREQUIRED,
                          spec->positionals.data[k].name.data,
                          spec->positionals.data[k].name.size));
        }
    }
    z_@Result_Parsed_CliError@_t r = {0};
    r.tag = Z_RESULT_PARSED_CLIERROR_TAG_OK;
    r.data = parsed;
    return r;
}

