static z_@String@_t z_cli_helpText(z_@Spec@_t* spec);
static z_@String@_t z_cli_helpText(z_@Spec@_t* spec) {
    z_@String@_t out = z_@String@_create(128);
    z_@String@_append(&out, "usage: ", 7);
    z_@String@_append(&out, spec->@MEMBER_Spec.programName@.data, spec->@MEMBER_Spec.programName@.size);
    z_@String@_append(&out, " [options]", 10);
    for (uint64_t i = 0; i < spec->@MEMBER_Spec.positionals@.length; i++) {
        z_@String@_append(&out, " <", 2);
        z_@String@_append(&out, spec->@MEMBER_Spec.positionals@.data[i].@MEMBER_PositionalDef.name@.data,
                        spec->@MEMBER_Spec.positionals@.data[i].@MEMBER_PositionalDef.name@.size);
        z_@String@_append(&out, ">", 1);
    }
    if (spec->@MEMBER_Spec.summary@.size > 0) {
        z_@String@_append(&out, "\n\n", 2);
        z_@String@_append(&out, spec->@MEMBER_Spec.summary@.data, spec->@MEMBER_Spec.summary@.size);
    }
    if (spec->@MEMBER_Spec.flags@.length > 0) {
        z_@String@_append(&out, "\n\nFlags:", 9);
        for (uint64_t i = 0; i < spec->@MEMBER_Spec.flags@.length; i++) {
            z_@FlagDef@_t* f = &spec->@MEMBER_Spec.flags@.data[i];
            z_@String@_append(&out, "\n  ", 3);
            if (f->@MEMBER_FlagDef.shortName@.size > 0) {
                z_@String@_append(&out, f->@MEMBER_FlagDef.shortName@.data, f->@MEMBER_FlagDef.shortName@.size);
                z_@String@_append(&out, ", ", 2);
            }
            z_@String@_append(&out, f->@MEMBER_FlagDef.name@.data, f->@MEMBER_FlagDef.name@.size);
            if (f->@MEMBER_FlagDef.help@.size > 0) {
                z_@String@_append(&out, "    ", 4);
                z_@String@_append(&out, f->@MEMBER_FlagDef.help@.data, f->@MEMBER_FlagDef.help@.size);
            }
        }
    }
    if (spec->@MEMBER_Spec.options@.length > 0) {
        z_@String@_append(&out, "\n\nOptions:", 11);
        for (uint64_t i = 0; i < spec->@MEMBER_Spec.options@.length; i++) {
            z_@OptionDef@_t* o = &spec->@MEMBER_Spec.options@.data[i];
            z_@String@_append(&out, "\n  ", 3);
            if (o->@MEMBER_OptionDef.shortName@.size > 0) {
                z_@String@_append(&out, o->@MEMBER_OptionDef.shortName@.data, o->@MEMBER_OptionDef.shortName@.size);
                z_@String@_append(&out, ", ", 2);
            }
            z_@String@_append(&out, o->@MEMBER_OptionDef.name@.data, o->@MEMBER_OptionDef.name@.size);
            if (o->@MEMBER_OptionDef.help@.size > 0) {
                z_@String@_append(&out, "    ", 4);
                z_@String@_append(&out, o->@MEMBER_OptionDef.help@.data, o->@MEMBER_OptionDef.help@.size);
            }
        }
    }
    if (spec->@MEMBER_Spec.positionals@.length > 0) {
        z_@String@_append(&out, "\n\nPositionals:", 15);
        for (uint64_t i = 0; i < spec->@MEMBER_Spec.positionals@.length; i++) {
            z_@PositionalDef@_t* p = &spec->@MEMBER_Spec.positionals@.data[i];
            z_@String@_append(&out, "\n  ", 3);
            z_@String@_append(&out, p->@MEMBER_PositionalDef.name@.data, p->@MEMBER_PositionalDef.name@.size);
            if (p->@MEMBER_PositionalDef.help@.size > 0) {
                z_@String@_append(&out, "    ", 4);
                z_@String@_append(&out, p->@MEMBER_PositionalDef.help@.data, p->@MEMBER_PositionalDef.help@.size);
            }
        }
    }
    return out;
}


