/* name/shortName/help are .take: stored directly in the optiondef. */
static void z_cli_addOption(z_@Spec@_t* spec,
    z_@String@_t name, z_@String@_t shortName, z_@String@_t help, bool required);
static void z_cli_addOption(z_@Spec@_t* spec,
    z_@String@_t name, z_@String@_t shortName, z_@String@_t help, bool required) {
    z_@OptionDef@_t o = {0};
    o.name = name;
    o.shortName = shortName;
    o.help = help;
    o.required = required;
    z_@List_OptionDef@_append(&spec->options, o);
}


