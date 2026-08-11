static z_@String@_t z_cli_helpText(z_@Spec@_t* spec);
static z_@String@_t z_cli_helpText(z_@Spec@_t* spec) {
    z_@String@_t out = z_@String@_create(128);
    z_@String@_append(&out, "usage: ", 7);
    z_@String@_append(&out, spec->programName.data, spec->programName.size);
    z_@String@_append(&out, " [options]", 10);
    for (uint64_t i = 0; i < spec->positionals.length; i++) {
        z_@String@_append(&out, " <", 2);
        z_@String@_append(&out, spec->positionals.data[i].name.data,
                        spec->positionals.data[i].name.size);
        z_@String@_append(&out, ">", 1);
    }
    if (spec->summary.size > 0) {
        z_@String@_append(&out, "\n\n", 2);
        z_@String@_append(&out, spec->summary.data, spec->summary.size);
    }
    if (spec->flags.length > 0) {
        z_@String@_append(&out, "\n\nFlags:", 9);
        for (uint64_t i = 0; i < spec->flags.length; i++) {
            z_@FlagDef@_t* f = &spec->flags.data[i];
            z_@String@_append(&out, "\n  ", 3);
            if (f->shortName.size > 0) {
                z_@String@_append(&out, f->shortName.data, f->shortName.size);
                z_@String@_append(&out, ", ", 2);
            }
            z_@String@_append(&out, f->name.data, f->name.size);
            if (f->help.size > 0) {
                z_@String@_append(&out, "    ", 4);
                z_@String@_append(&out, f->help.data, f->help.size);
            }
        }
    }
    if (spec->options.length > 0) {
        z_@String@_append(&out, "\n\nOptions:", 11);
        for (uint64_t i = 0; i < spec->options.length; i++) {
            z_@OptionDef@_t* o = &spec->options.data[i];
            z_@String@_append(&out, "\n  ", 3);
            if (o->shortName.size > 0) {
                z_@String@_append(&out, o->shortName.data, o->shortName.size);
                z_@String@_append(&out, ", ", 2);
            }
            z_@String@_append(&out, o->name.data, o->name.size);
            if (o->help.size > 0) {
                z_@String@_append(&out, "    ", 4);
                z_@String@_append(&out, o->help.data, o->help.size);
            }
        }
    }
    if (spec->positionals.length > 0) {
        z_@String@_append(&out, "\n\nPositionals:", 15);
        for (uint64_t i = 0; i < spec->positionals.length; i++) {
            z_@PositionalDef@_t* p = &spec->positionals.data[i];
            z_@String@_append(&out, "\n  ", 3);
            z_@String@_append(&out, p->name.data, p->name.size);
            if (p->help.size > 0) {
                z_@String@_append(&out, "    ", 4);
                z_@String@_append(&out, p->help.data, p->help.size);
            }
        }
    }
    return out;
}

