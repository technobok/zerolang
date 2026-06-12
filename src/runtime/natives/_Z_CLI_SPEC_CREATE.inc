/* `programName` and `summary` are .take params: caller transfers
   ownership of the string buffers; the spec stores them directly
   (no copy). Caller-side implicit-take zeros the source vars. */
static z_Spec_t z_Spec_create(z_String_t programName, z_String_t summary);
static z_Spec_t z_Spec_create(z_String_t programName, z_String_t summary) {
    z_Spec_t s = {0};
    s.programName = programName;
    s.summary = summary;
    s.flags = z_List_FlagDef_create(0);
    s.options = z_List_OptionDef_create(0);
    s.positionals = z_List_PositionalDef_create(0);
    return s;
}

