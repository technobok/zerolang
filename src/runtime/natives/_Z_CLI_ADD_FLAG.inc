/* name/shortName/help are .take: stored directly in the flagdef. */
static void z_cli_addFlag(z_@Spec@_t* spec,
    z_@String@_t name, z_@String@_t shortName, z_@String@_t help);
static void z_cli_addFlag(z_@Spec@_t* spec,
    z_@String@_t name, z_@String@_t shortName, z_@String@_t help) {
    z_@FlagDef@_t f = {0};
    f.@MEMBER_FlagDef.name@ = name;
    f.@MEMBER_FlagDef.shortName@ = shortName;
    f.@MEMBER_FlagDef.help@ = help;
    z_@List_FlagDef@_append(&spec->@MEMBER_Spec.flags@, f);
}


