/* `programName` and `summary` are .take params: caller transfers
   ownership of the string buffers; the spec stores them directly
   (no copy). Caller-side implicit-take zeros the source vars. */
static z_@Spec@_t z_@Spec@_create(z_@String@_t programName, z_@String@_t summary);
static z_@Spec@_t z_@Spec@_create(z_@String@_t programName, z_@String@_t summary) {
    z_@Spec@_t s = {0};
    s.programName = programName;
    s.summary = summary;
    s.flags = z_@List_FlagDef@_create(0);
    s.options = z_@List_OptionDef@_create(0);
    s.positionals = z_@List_PositionalDef@_create(0);
    return s;
}


