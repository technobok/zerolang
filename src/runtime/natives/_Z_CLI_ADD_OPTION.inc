/* name/shortName/help are .take: stored directly in the optiondef. */
static void z_cli_addOption(z_@Spec@_t* spec,
    z_@String@_t name, z_@String@_t shortName, z_@String@_t help, bool required);
static void z_cli_addOption(z_@Spec@_t* spec,
    z_@String@_t name, z_@String@_t shortName, z_@String@_t help, bool required) {
    z_@OptionDef@_t o = {0};
    o.@MEMBER_OptionDef.name@ = name;
    o.@MEMBER_OptionDef.shortName@ = shortName;
    o.@MEMBER_OptionDef.help@ = help;
    o.@MEMBER_OptionDef.required@ = required;
    z_@List_OptionDef@_append(&spec->@MEMBER_Spec.options@, o);
}


