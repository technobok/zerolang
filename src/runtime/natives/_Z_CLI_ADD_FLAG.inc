/* name/shortName/help are .take: stored directly in the flagdef. */
static void z_cli_addFlag(z_Spec_t* spec,
    z_String_t name, z_String_t shortName, z_String_t help);
static void z_cli_addFlag(z_Spec_t* spec,
    z_String_t name, z_String_t shortName, z_String_t help) {
    z_FlagDef_t f = {0};
    f.name = name;
    f.shortName = shortName;
    f.help = help;
    z_List_FlagDef_append(&spec->flags, f);
}

