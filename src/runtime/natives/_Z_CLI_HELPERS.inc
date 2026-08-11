/* byte-compare z_@String@_t vs C string + length */
static bool z_cli_str_eq(const z_@String@_t* a, const char* b, uint64_t blen);
static bool z_cli_str_eq(const z_@String@_t* a, const char* b, uint64_t blen) {
    if (a->size != blen) return false;
    return memcmp(a->data, b, blen) == 0;
}

/* find flag index by long name */
static int64_t z_cli_find_flag_long(const z_@List_FlagDef@_t* flags, const char* name, uint64_t nlen);
static int64_t z_cli_find_flag_long(const z_@List_FlagDef@_t* flags, const char* name, uint64_t nlen) {
    for (uint64_t i = 0; i < flags->length; i++) {
        if (z_cli_str_eq(&flags->data[i].name, name, nlen)) return (int64_t)i;
    }
    return -1;
}

/* find flag index by short name (full short incl. `-`) */
static int64_t z_cli_find_flag_short(const z_@List_FlagDef@_t* flags, const char* name, uint64_t nlen);
static int64_t z_cli_find_flag_short(const z_@List_FlagDef@_t* flags, const char* name, uint64_t nlen) {
    for (uint64_t i = 0; i < flags->length; i++) {
        if (z_cli_str_eq(&flags->data[i].shortName, name, nlen)) return (int64_t)i;
    }
    return -1;
}

static int64_t z_cli_findOption_long(const z_@List_OptionDef@_t* options, const char* name, uint64_t nlen);
static int64_t z_cli_findOption_long(const z_@List_OptionDef@_t* options, const char* name, uint64_t nlen) {
    for (uint64_t i = 0; i < options->length; i++) {
        if (z_cli_str_eq(&options->data[i].name, name, nlen)) return (int64_t)i;
    }
    return -1;
}

static int64_t z_cli_findOption_short(const z_@List_OptionDef@_t* options, const char* name, uint64_t nlen);
static int64_t z_cli_findOption_short(const z_@List_OptionDef@_t* options, const char* name, uint64_t nlen) {
    for (uint64_t i = 0; i < options->length; i++) {
        if (z_cli_str_eq(&options->data[i].shortName, name, nlen)) return (int64_t)i;
    }
    return -1;
}

/* build a clierror union with a string payload */
static z_@CliError@_t z_cli_err(z_@CliError@_tag_t tag, const char* msg, uint64_t mlen);
static z_@CliError@_t z_cli_err(z_@CliError@_tag_t tag, const char* msg, uint64_t mlen) {
    z_@CliError@_t e = {0};
    e.tag = tag;
    z_@String@_t* s = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *s = z_@String@_create(mlen);
    z_@String@_append(s, msg, mlen);
    e.data = s;
    return e;
}

/* wrap clierror in result.err */
static z_@Result_Parsed_CliError@_t z_cli_wrap_err(z_@CliError@_t e);
static z_@Result_Parsed_CliError@_t z_cli_wrap_err(z_@CliError@_t e) {
    z_@Result_Parsed_CliError@_t r = {0};
    z_@CliError@_t* boxed = (z_@CliError@_t*)z_xmalloc(sizeof(z_@CliError@_t));
    *boxed = e;
    r.tag = Z_RESULT_PARSED_CLIERROR_TAG_ERR;
    r.data = boxed;
    return r;
}

