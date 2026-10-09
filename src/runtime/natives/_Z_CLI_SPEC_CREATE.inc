/* `programName` and `summary` are .take params: caller transfers
   ownership of the string buffers; the spec stores them directly
   (no copy). Caller-side implicit-take zeros the source vars. */
static z_@Spec@_t z_@Spec@_create(z_@String@_t programName, z_@String@_t summary);
static z_@Spec@_t z_@Spec@_create(z_@String@_t programName, z_@String@_t summary) {
    z_@Spec@_t s = {0};
    s.@MEMBER_Spec.programName@ = programName;
    s.@MEMBER_Spec.summary@ = summary;
    s.@MEMBER_Spec.flags@ = z_@List_FlagDef@_create(0);
    s.@MEMBER_Spec.options@ = z_@List_OptionDef@_create(0);
    s.@MEMBER_Spec.positionals@ = z_@List_PositionalDef@_create(0);
    return s;
}


