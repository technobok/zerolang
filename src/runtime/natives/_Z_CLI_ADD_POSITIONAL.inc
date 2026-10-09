/* name/help are .take: stored directly in the positionaldef. */
static void z_cli_addPositional(z_@Spec@_t* spec,
    z_@String@_t name, z_@String@_t help, bool required);
static void z_cli_addPositional(z_@Spec@_t* spec,
    z_@String@_t name, z_@String@_t help, bool required) {
    z_@PositionalDef@_t p = {0};
    p.@MEMBER_PositionalDef.name@ = name;
    p.@MEMBER_PositionalDef.help@ = help;
    p.@MEMBER_PositionalDef.required@ = required;
    z_@List_PositionalDef@_append(&spec->@MEMBER_Spec.positionals@, p);
}


