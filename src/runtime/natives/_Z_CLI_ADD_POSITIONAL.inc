/* name/help are .take: stored directly in the positionaldef. */
static void z_cli_addPositional(z_Spec_t* spec,
    z_String_t name, z_String_t help, bool required);
static void z_cli_addPositional(z_Spec_t* spec,
    z_String_t name, z_String_t help, bool required) {
    z_PositionalDef_t p = {0};
    p.name = name;
    p.help = help;
    p.required = required;
    z_List_PositionalDef_append(&spec->positionals, p);
}

