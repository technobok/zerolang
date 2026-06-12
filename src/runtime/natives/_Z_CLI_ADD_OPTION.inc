/* name/shortName/help are .take: stored directly in the optiondef. */
static void z_cli_addOption(z_Spec_t* spec,
    z_String_t name, z_String_t shortName, z_String_t help, bool required);
static void z_cli_addOption(z_Spec_t* spec,
    z_String_t name, z_String_t shortName, z_String_t help, bool required) {
    z_OptionDef_t o = {0};
    o.name = name;
    o.shortName = shortName;
    o.help = help;
    o.required = required;
    z_List_OptionDef_append(&spec->options, o);
}

