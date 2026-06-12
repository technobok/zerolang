static z_String_t z_cli_helpText(z_Spec_t* spec);
static z_String_t z_cli_helpText(z_Spec_t* spec) {
    z_String_t out = z_String_create(128);
    z_String_append(&out, "usage: ", 7);
    z_String_append(&out, spec->programName.data, spec->programName.size);
    z_String_append(&out, " [options]", 10);
    for (uint64_t i = 0; i < spec->positionals.length; i++) {
        z_String_append(&out, " <", 2);
        z_String_append(&out, spec->positionals.data[i].name.data,
                        spec->positionals.data[i].name.size);
        z_String_append(&out, ">", 1);
    }
    if (spec->summary.size > 0) {
        z_String_append(&out, "\n\n", 2);
        z_String_append(&out, spec->summary.data, spec->summary.size);
    }
    if (spec->flags.length > 0) {
        z_String_append(&out, "\n\nFlags:", 9);
        for (uint64_t i = 0; i < spec->flags.length; i++) {
            z_FlagDef_t* f = &spec->flags.data[i];
            z_String_append(&out, "\n  ", 3);
            if (f->shortName.size > 0) {
                z_String_append(&out, f->shortName.data, f->shortName.size);
                z_String_append(&out, ", ", 2);
            }
            z_String_append(&out, f->name.data, f->name.size);
            if (f->help.size > 0) {
                z_String_append(&out, "    ", 4);
                z_String_append(&out, f->help.data, f->help.size);
            }
        }
    }
    if (spec->options.length > 0) {
        z_String_append(&out, "\n\nOptions:", 11);
        for (uint64_t i = 0; i < spec->options.length; i++) {
            z_OptionDef_t* o = &spec->options.data[i];
            z_String_append(&out, "\n  ", 3);
            if (o->shortName.size > 0) {
                z_String_append(&out, o->shortName.data, o->shortName.size);
                z_String_append(&out, ", ", 2);
            }
            z_String_append(&out, o->name.data, o->name.size);
            if (o->help.size > 0) {
                z_String_append(&out, "    ", 4);
                z_String_append(&out, o->help.data, o->help.size);
            }
        }
    }
    if (spec->positionals.length > 0) {
        z_String_append(&out, "\n\nPositionals:", 15);
        for (uint64_t i = 0; i < spec->positionals.length; i++) {
            z_PositionalDef_t* p = &spec->positionals.data[i];
            z_String_append(&out, "\n  ", 3);
            z_String_append(&out, p->name.data, p->name.size);
            if (p->help.size > 0) {
                z_String_append(&out, "    ", 4);
                z_String_append(&out, p->help.data, p->help.size);
            }
        }
    }
    return out;
}

