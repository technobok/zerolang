"""
Generator-function desugaring (Phase G3).

Runs after parsing and before type resolution. Walks every parsed
function looking for *generators* — functions whose `out` type is
`iterator gives: T (takes: U)` AND whose body contains at least one
`yield` expression. For each generator, this pass:

  1. Validates the parameter ownership annotations (bare `.borrow` is
     rejected; `:this` on methods must be `:this.lock` or
     `:this.private.lock`).
  2. Validates the body (no `return <value>`; `yield` is not allowed
     inside a nested function literal — already enforced by the
     parser via function-body depth tracking).
  3. Synthesises a class whose `.call` method implements the
     iterator protocol structurally — the class's fields capture the
     original parameters (with matching ownership) plus a `state`
     cursor, and the method body is the original function's body
     with all promoted-name references rewritten to `this.<name>`.
  4. Rewrites the original function declaration into a *factory*
     that calls `<synth_class>.create` on the captured parameters.

The synthesized class's `.call` body keeps its `Yield` nodes intact.
The typechecker tolerates yields in this position (their expression
is type-checked against `gives`; the body's implicit-return check is
skipped). The actual state-machine codegen — `switch (this->state) {
case ...: goto L_resumeN; }` — is the emitter's job in G4.

Out-only generators only (`takes` defaults to `null`). Bidirectional
`takes != null` lands in G6.
"""

from typing import Dict, List, Optional, Set, Tuple, cast

import zast
from zast import (
    ERR,
    NodeType,
    AtomId,
    Call,
    DottedPath,
    Expression,
    Function,
    NamedOperation,
    ObjectDef,
    Path,
    Statement,
    StatementLine,
    Yield,
)
from zlexer import Token


_SYNTH_ORIGIN = "generator"


def desugar_generators(program: zast.Program) -> List[zast.Error]:
    """Top-level entry: walk `program` and desugar every generator
    found, mutating `program.units` in place. Returns the list of
    errors emitted during validation (empty list on success).

    The pass is idempotent: running it twice on the same program is
    a no-op the second time because every generator has been
    rewritten into a non-generator factory.
    """
    errors: List[zast.Error] = []
    for unit in program.units.values():
        _desugar_unit(unit, errors)
    return errors


def _desugar_unit(unit: zast.Unit, errors: List[zast.Error]) -> None:
    # The unit body holds named definitions. We collect every
    # generator-shaped function in two passes so we can splice in
    # the synthesized class without disturbing iteration.
    additions: Dict[str, zast.TypeDefinition] = {}
    replacements: Dict[str, Function] = {}
    for name, defn in list(unit.body.items()):
        if defn.nodetype == NodeType.FUNCTION:
            func = cast(Function, defn)
            if _is_generator_function(func):
                synth_name = _synth_class_name(name, additions, unit.body)
                synth_class, factory = _build_generator(synth_name, func, errors)
                if synth_class is None or factory is None:
                    # validation rejected this generator; original
                    # function stays in place so downstream passes
                    # don't crash on a half-rewritten definition.
                    continue
                additions[synth_name] = synth_class
                replacements[name] = factory
        elif defn.nodetype in (
            NodeType.RECORD,
            NodeType.CLASS,
            NodeType.UNION,
            NodeType.VARIANT,
            NodeType.PROTOCOL,
            NodeType.FACET,
            NodeType.ENUM,
        ):
            _desugar_object_def(name, cast(ObjectDef, defn), unit, errors, additions)

    for name, fn in replacements.items():
        unit.body[name] = fn
    for name, defn in additions.items():
        unit.body[name] = defn


def _desugar_object_def(
    type_name: str,
    objdef: ObjectDef,
    unit: zast.Unit,
    errors: List[zast.Error],
    unit_additions: Dict[str, zast.TypeDefinition],
) -> None:
    """Desugar generator methods on a type.

    A generator method `m` on type `T` is split:
      - `T.m_iter`: synthesised class placed at unit level next to T
      - `T.m`: rewritten in place as a factory returning that class

    Method-context generators only differ from top-level ones in
    that `:this.lock` / `:this.private.lock` parameters are
    captured as receiver-lock fields named after the parameter
    (typically `t`).
    """
    for items_block in (objdef.is_items, objdef.as_items):
        for mname, mdefn in list(items_block.items()):
            if mdefn.nodetype != NodeType.FUNCTION:
                continue
            mfunc = cast(Function, mdefn)
            if not _is_generator_function(mfunc):
                continue
            synth_name = _synth_class_name(
                f"{type_name}_{mname}", unit_additions, unit.body
            )
            synth_class, factory = _build_generator(
                synth_name, mfunc, errors, receiver_type=type_name
            )
            if synth_class is None or factory is None:
                continue
            unit_additions[synth_name] = synth_class
            items_block[mname] = factory


def _is_generator_function(func: Function) -> bool:
    """A function is a generator iff:
    (a) its declared return type is `iterator gives: T (takes: U)`,
        recognised structurally on the parsed return-type AST, AND
    (b) its body contains at least one `yield` expression.
    """
    if func.body is None:
        return False  # spec / native — never a generator
    if not _returntype_is_iterator(func.returntype):
        return False
    return _body_contains_yield(func.body)


def _returntype_is_iterator(rt: Optional[Path]) -> bool:
    """The return type AST is `iterator gives: ...` when the parsed
    path is `Expression(Call(callable=AtomId("iterator"), args=...))`.

    The check is intentionally syntactic — it must run *before* type
    resolution, so `iterator` is recognised by the lexeme. Aliasing
    `iterator` to some other name in a non-system unit would skip
    this branch; that's deliberate — generator synthesis is a
    stdlib-coupled feature.
    """
    if rt is None or rt.nodetype != NodeType.EXPRESSION:
        return False
    inner = cast(Expression, rt).expression
    if inner.nodetype != NodeType.CALL:
        return False
    call = cast(Call, inner)
    if call.callable.nodetype != NodeType.ATOMID:
        return False
    return (
        cast(AtomId, call.callable).name
        == "iterator"  # ztc-string-compare-ok: iterator marker
    )


def _body_contains_yield(stmt: zast.Node) -> bool:
    """Walk a statement subtree returning True iff a `Yield` node is
    present anywhere within. Stops descending into nested function
    literals — yields lexically belong to the enclosing function
    only (parser rule 10)."""
    if stmt.nodetype == NodeType.YIELD:
        return True
    if stmt.nodetype == NodeType.FUNCTION:
        return False  # nested function literal — not our yields
    for child in zast.node_children(stmt):
        if _body_contains_yield(child):
            return True
    return False


def _gives_arg(rt: Path) -> Optional[NamedOperation]:
    """Extract the `gives:` argument from an iterator return-type
    Call. Caller must have verified `_returntype_is_iterator` first."""
    inner = cast(Expression, rt).expression
    call = cast(Call, inner)
    for arg in call.arguments:
        if arg.name == "gives":  # ztc-string-compare-ok: iterator protocol param name
            return arg
    return None


# Ownership suffix recognised on a `gives:` path leaf.
_GIVES_OWNERSHIP_LEAVES = {"take", "borrow"}


def _gives_form(rt: Path) -> Tuple[Optional[Path], Optional[str]]:
    """Return `(base_type_path, ownership_leaf_or_None)` for the
    `gives:` argument. Bare T returns `(T, None)`; T.take/T.borrow
    return the stripped path and the leaf name.

    Other DottedPath leaves (e.g. `T.lock`, `T.private`) pass through
    untouched — they were already rejected by the typechecker's
    iterator-gives validator and won't reach this point in a green
    compile."""
    arg = _gives_arg(rt)
    if arg is None:
        return None, None
    val = arg.valtype
    if val.nodetype == NodeType.DOTTEDPATH:
        dp = cast(DottedPath, val)
        leaf = dp.child.name
        if leaf in _GIVES_OWNERSHIP_LEAVES:
            return dp.parent, leaf  # ztc-string-compare-ok: ownership-suffix membership
    # Other Path shapes (AtomId / LabelValue / AtomString / Expression
    # / BinOp) carry no ownership suffix — return them as the base.
    if val.nodetype in (
        NodeType.ATOMID,
        NodeType.LABELVALUE,
        NodeType.DOTTEDPATH,
        NodeType.ATOMSTRING,
        NodeType.EXPRESSION,
    ):
        return cast(Path, val), None
    return None, None


def _option_wrapper_for_gives(rt: Path) -> Optional[Call]:
    """Synthesise the `.call` return-type AST for a generator
    whose `gives:` argument has the form recognised in `_gives_form`.

    Mapping (per the plan's table):
        bare T          -> (optionval t: T)
        T.take          -> (Option t: T)
        T.borrow        -> (OptionView t: T)
    """
    base, leaf = _gives_form(rt)
    if base is None:
        return None
    if leaf == "take":  # ztc-string-compare-ok: ownership-suffix marker
        wrapper_name = "Option"
    elif leaf == "borrow":  # ztc-string-compare-ok: ownership-suffix marker
        wrapper_name = "OptionView"
    else:
        wrapper_name = "optionval"
    start = base.start
    callable_node = AtomId(name=wrapper_name, start=start, synth_origin=_SYNTH_ORIGIN)
    arg = NamedOperation(
        name="t",
        valtype=cast(zast.Operation, base),
        start=start,
        synth_origin=_SYNTH_ORIGIN,
    )
    return Call(
        callable=callable_node,
        arguments=[arg],
        start=start,
        synth_origin=_SYNTH_ORIGIN,
    )


# ---- Parameter-ownership validation -------------------------------


_OWNERSHIP_LEAVES = {"take", "lock", "borrow"}
_VALID_GENERATOR_PARAM_LEAVES = {"take", "lock"}


def _split_path_leaf(p: zast.Operation) -> Tuple[zast.Operation, Optional[str]]:
    """Return `(base, leaf_or_None)` where `leaf` is one of
    take/lock/borrow if the path ends in such a suffix."""
    if p.nodetype == NodeType.DOTTEDPATH:
        dp = cast(DottedPath, p)
        if dp.child.name in _OWNERSHIP_LEAVES:
            return (
                dp.parent,
                dp.child.name,
            )  # ztc-string-compare-ok: ownership-suffix membership
    return p, None


def _is_this_path(p: zast.Operation) -> bool:
    """`this` reaches us as either a plain AtomId (e.g. a parameter
    typed `t: this`) or a LabelValue when the user wrote the
    `:this` shorthand. Both are atoms with name `this`."""
    return (
        p.nodetype in (NodeType.ATOMID, NodeType.LABELVALUE)
        and cast(AtomId, p).name == "this"  # ztc-string-compare-ok: this keyword
    )


def _is_this_private_path(p: zast.Operation) -> bool:
    if p.nodetype != NodeType.DOTTEDPATH:
        return False
    dp = cast(DottedPath, p)
    return (
        _is_this_path(dp.parent)
        and dp.child.name == "private"  # ztc-string-compare-ok: private accessor
    )


def _validate_parameter(
    name: str, ppath: Path, start: Token, errors: List[zast.Error]
) -> bool:
    """Per the parameter-ownership table for generators:

        T.take              OK     owned field
        T.lock              OK     locked field
        :this.lock          OK     locked receiver field
        :this.private.lock  OK     friend-access receiver lock
        T (reftype, bare)   ERR    captures with no owning story
        T.borrow            ERR    borrow lifetime ends with factory
        :this (bare)        ERR    same as bare borrow

    Returns True if the parameter is valid for a generator.

    Reftype-vs-valtype discrimination happens during type-resolution,
    not here — at parse time we can't tell if `T` is a record (OK
    bare) or a class (REJECT bare). We accept bare here and let the
    later typecheck pass catch reftype-bare-borrow if it slipped
    through.
    """
    base, leaf = _split_path_leaf(ppath)

    # :this / :this.private receiver parameters
    if _is_this_path(base) or _is_this_private_path(base):
        if leaf is None:
            errors.append(
                zast.Error(
                    start=ppath.start,
                    err=ERR.OWNERERROR,
                    msg=(
                        "generator method receiver must be ':this.lock' "
                        "(or ':this.private.lock' for friend access); "
                        "bare ':this' is not legal — the iterator outlives "
                        "the factory call."
                    ),
                )
            )
            return False
        if leaf != "lock":  # ztc-string-compare-ok: ownership-suffix marker
            errors.append(
                zast.Error(
                    start=ppath.start,
                    err=ERR.OWNERERROR,
                    msg=(
                        f"generator method receiver may not use '.{leaf}'; "
                        "use ':this.lock' (or ':this.private.lock' for "
                        "friend access)."
                    ),
                )
            )
            return False
        return True

    # ordinary parameters: bare is allowed (we accept valtypes; reftype
    # rejection requires post-resolution type info we don't have here).
    if leaf is None:
        return True
    if leaf == "borrow":  # ztc-string-compare-ok: ownership-suffix marker
        errors.append(
            zast.Error(
                start=ppath.start,
                err=ERR.OWNERERROR,
                msg=(
                    f"generator parameter '{name}' cannot be '.borrow' — "
                    "the borrow's lifetime is the factory call, but the "
                    "iterator outlives that call. Use '.lock' (locked "
                    "field) or '.take' (owned field) instead."
                ),
            )
        )
        return False
    if leaf not in _VALID_GENERATOR_PARAM_LEAVES:
        errors.append(
            zast.Error(
                start=ppath.start,
                err=ERR.OWNERERROR,
                msg=(
                    f"generator parameter '{name}' has unsupported "
                    f"ownership suffix '.{leaf}'"
                ),
            )
        )
        return False
    return True


# ---- Body validation ---------------------------------------------


def _validate_body(
    stmt: zast.Node, errors: List[zast.Error], in_nested_fn: bool = False
) -> None:
    """Walk the generator body looking for forbidden constructs:

    - `return <value>` (bare `return` is OK — it terminates the
      generator).
    - `yield` inside a nested function literal — already caught by
      the parser, but we re-check defensively in case a synthetic
      pass produced one.
    """
    nt = stmt.nodetype
    if nt == NodeType.YIELD and in_nested_fn:
        errors.append(
            zast.Error(
                start=stmt.start,
                err=ERR.BADSTATEMENT,
                msg=(
                    "'yield' is not allowed inside a nested function "
                    "literal; it belongs to the enclosing generator only."
                ),
            )
        )
        return
    if nt == NodeType.CALL:
        call = cast(Call, stmt)
        # `return <value>` is parsed as a Call whose callable is
        # AtomId("return"); a bare `return` is just an AtomId.
        if (
            call.callable.nodetype == NodeType.ATOMID
            and cast(AtomId, call.callable).name
            == "return"  # ztc-string-compare-ok: return keyword
            and call.arguments
        ):
            errors.append(
                zast.Error(
                    start=stmt.start,
                    err=ERR.BADSTATEMENT,
                    msg=(
                        "'return <value>' is not allowed inside a "
                        "generator; yielded values exit via 'yield' and "
                        "bare 'return' terminates the generator."
                    ),
                )
            )
    if nt == NodeType.FUNCTION:
        for child in zast.node_children(stmt):
            _validate_body(child, errors, in_nested_fn=True)
        return
    for child in zast.node_children(stmt):
        _validate_body(child, errors, in_nested_fn=in_nested_fn)


# ---- Local-name collection (promote-everything in v1) ------------


def _collect_assigned_locals(body: Statement) -> List[str]:
    """Walk the generator body collecting names introduced via
    `name: <expr>` assignment statements. Order preserved.

    Promote-everything mode (G3 v1): every local crossing a yield
    *and* every local that doesn't gets promoted to a field. The
    liveness-aware refinement is G7.
    """
    seen: Set[str] = set()
    order: List[str] = []

    def walk(node: zast.Node) -> None:
        nt = node.nodetype
        if nt == NodeType.FUNCTION:
            return  # don't recurse into nested function literals
        if nt == NodeType.ASSIGNMENT:
            assn = cast(zast.Assignment, node)
            if assn.name not in seen:
                seen.add(assn.name)
                order.append(assn.name)
        for c in zast.node_children(node):
            walk(c)

    walk(body)
    return order


# ---- Body rewrite: AtomId(X) -> this.X for promoted names --------


def _rewrite_to_this(node: zast.Node, promoted: Set[str]) -> zast.Node:
    """Clone `node` rewriting every AtomId/LABELVALUE whose name is
    in `promoted` into `this.<name>` (a DottedPath).

    Only Path-position identifiers get rewritten — labels in
    NamedOperations are syntactic argument names, not value
    references, and stay as-is.
    """
    nt = node.nodetype

    if nt == NodeType.ATOMID:
        ai = cast(AtomId, node)
        if ai.name in promoted:
            return _make_this_dotted(ai.name, ai.start)
        return ai

    if nt == NodeType.LABELVALUE:
        lv = cast(zast.LabelValue, node)
        if lv.name in promoted:
            # `:foo` shorthand becomes `foo: this.foo` at the Assignment
            # parent — but at the value-position itself we just emit
            # `this.foo`. The wrapping NamedOperation/Assignment keeps
            # the original label name.
            return _make_this_dotted(lv.name, lv.start)
        return lv

    if nt == NodeType.DOTTEDPATH:
        dp = cast(DottedPath, node)
        # rewrite the head only; child names are field labels, not
        # value identifiers.
        new_parent = _rewrite_to_this(dp.parent, promoted)
        if new_parent is dp.parent:
            return dp
        return DottedPath(
            parent=cast(Path, new_parent),
            child=dp.child,
            start=dp.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.EXPRESSION:
        e = cast(Expression, node)
        new_inner = _rewrite_to_this(e.expression, promoted)
        if new_inner is e.expression:
            return e
        return Expression(
            expression=cast(zast.ExpressionSubTypes, new_inner),
            start=e.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.CALL:
        c = cast(Call, node)
        new_callable = _rewrite_to_this(c.callable, promoted)
        new_args = [
            cast(NamedOperation, _rewrite_to_this(a, promoted)) for a in c.arguments
        ]
        return Call(
            callable=cast(Path, new_callable),
            arguments=new_args,
            start=c.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.NAMEDOPERATION:
        no = cast(NamedOperation, node)
        new_val = _rewrite_to_this(no.valtype, promoted)
        if new_val is no.valtype:
            return no
        return NamedOperation(
            name=no.name,
            valtype=cast(zast.Operation, new_val),
            start=no.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.BINOP:
        bo = cast(zast.BinOp, node)
        return zast.BinOp(
            lhs=cast(zast.Operation, _rewrite_to_this(bo.lhs, promoted)),
            operator=bo.operator,
            rhs=cast(Path, _rewrite_to_this(bo.rhs, promoted)),
            start=bo.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.STATEMENT:
        s = cast(Statement, node)
        new_stmts = [
            cast(StatementLine, _rewrite_to_this(sl, promoted)) for sl in s.statements
        ]
        return Statement(
            statements=new_stmts,
            start=s.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.STATEMENTLINE:
        sl = cast(StatementLine, node)
        new_inner = _rewrite_to_this(sl.statementline, promoted)
        if new_inner is sl.statementline:
            return sl
        # StatementLine.statementline is constrained to a fixed
        # union (Assignment | Reassignment | Swap | Expression);
        # the rewrite preserves that shape, but the static return
        # type of `_rewrite_to_this` is `Node` so we narrow here.
        new_inner_typed = cast(
            "zast.Assignment | zast.Reassignment | zast.Swap | zast.Expression",
            new_inner,
        )
        return StatementLine(
            statementline=new_inner_typed,
            start=sl.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.ASSIGNMENT:
        # `local: <expr>` inside the body becomes
        # `this.local = <expr_rewritten>` (a Reassignment) — the field
        # was reserved at create time so this is an update, not a
        # fresh binding. We just rewrite the RHS here and emit a
        # Reassignment node so the field gets written through `this`.
        assn = cast(zast.Assignment, node)
        new_val = _rewrite_to_this(assn.value, promoted)
        if assn.name in promoted:
            topath = _make_this_dotted(assn.name, assn.start)
            re = zast.Reassignment(
                topath=topath,
                value=cast(Expression, new_val),
                start=assn.start,
                synth_origin=_SYNTH_ORIGIN,
            )
            return re
        if new_val is assn.value:
            return assn
        return zast.Assignment(
            name=assn.name,
            value=cast(Expression, new_val),
            start=assn.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.REASSIGNMENT:
        ra = cast(zast.Reassignment, node)
        new_topath = _rewrite_to_this(ra.topath, promoted)
        new_val = _rewrite_to_this(ra.value, promoted)
        return zast.Reassignment(
            topath=cast(Path, new_topath),
            value=cast(Expression, new_val),
            start=ra.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.SWAP:
        sw = cast(zast.Swap, node)
        return zast.Swap(
            lhs=cast(Path, _rewrite_to_this(sw.lhs, promoted)),
            rhs=cast(Path, _rewrite_to_this(sw.rhs, promoted)),
            start=sw.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.IF:
        ifn = cast(zast.If, node)
        new_clauses = [
            zast.IfClause(
                conditions={
                    k: cast(zast.Operation, _rewrite_to_this(v, promoted))
                    for k, v in c.conditions.items()
                },
                statement=cast(Statement, _rewrite_to_this(c.statement, promoted)),
                start=c.start,
                synth_origin=_SYNTH_ORIGIN,
            )
            for c in ifn.clauses
        ]
        new_else = (
            cast(Statement, _rewrite_to_this(ifn.elseclause, promoted))
            if ifn.elseclause is not None
            else None
        )
        return zast.If(
            clauses=new_clauses,
            elseclause=new_else,
            start=ifn.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.FOR:
        fn2 = cast(zast.For, node)
        return zast.For(
            conditions={
                k: cast(zast.Operation, _rewrite_to_this(v, promoted))
                for k, v in fn2.conditions.items()
            },
            loop=(
                cast(Statement, _rewrite_to_this(fn2.loop, promoted))
                if fn2.loop is not None
                else None
            ),
            postconditions=[
                cast(zast.Operation, _rewrite_to_this(pc, promoted))
                for pc in fn2.postconditions
            ],
            start=fn2.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.DO:
        do = cast(zast.Do, node)
        return zast.Do(
            statement=cast(Statement, _rewrite_to_this(do.statement, promoted)),
            start=do.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.WITH:
        w = cast(zast.With, node)
        return zast.With(
            name=w.name,
            value=cast(Expression, _rewrite_to_this(w.value, promoted)),
            doexpr=cast(Expression, _rewrite_to_this(w.doexpr, promoted)),
            start=w.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    if nt == NodeType.YIELD:
        y = cast(Yield, node)
        new_expr = _rewrite_to_this(y.expr, promoted)
        return Yield(
            expr=cast(Expression, new_expr),
            start=y.start,
            synth_origin=_SYNTH_ORIGIN,
        )

    # Anything else (atom strings, literal-only forms, etc.) passes
    # through unchanged.
    return node


def _make_this_dotted(name: str, loc: Token) -> DottedPath:
    """Build the `this.<name>` AST."""
    this_atom = AtomId(name="this", start=loc, synth_origin=_SYNTH_ORIGIN)
    child_atom = AtomId(name=name, start=loc, synth_origin=_SYNTH_ORIGIN)
    return DottedPath(
        parent=this_atom,
        child=child_atom,
        start=loc,
        synth_origin=_SYNTH_ORIGIN,
    )


# ---- Synthesized class / factory assembly ------------------------


def _synth_class_name(
    base: str,
    additions: Dict[str, zast.TypeDefinition],
    existing: Dict[str, zast.TypeDefinition],
) -> str:
    """Pick a unique synthesized-class name. Defaults to
    `<base>_iter` and disambiguates with a numeric suffix on
    collision (rare in practice)."""
    candidate = f"{base}_iter"
    if candidate not in additions and candidate not in existing:
        return candidate
    n = 2
    while True:
        candidate = f"{base}_iter{n}"
        if candidate not in additions and candidate not in existing:
            return candidate
        n += 1


def _build_generator(
    synth_name: str,
    func: Function,
    errors: List[zast.Error],
    receiver_type: Optional[str] = None,
) -> Tuple[Optional[ObjectDef], Optional[Function]]:
    """Build the synthesized class and rewritten factory for one
    generator. Returns `(None, None)` if validation rejected the
    generator (errors already appended to `errors`)."""
    assert func.body is not None
    # 1. validate parameters
    ok = True
    for pname, ppath in func.parameters.items():
        if not _validate_parameter(pname, ppath, func.start, errors):
            ok = False
    # 2. validate body
    pre_count = len(errors)
    _validate_body(func.body, errors)
    if len(errors) != pre_count:
        ok = False
    if not ok:
        return None, None

    # 3. determine the .call return type (option-wrapper around gives)
    call_return = _option_wrapper_for_gives(cast(Path, func.returntype))
    if call_return is None:
        # gives form unrecognised — typechecker already complained
        return None, None

    # 4. Promotion (v1): parameters only. Locals stay on the C stack
    # inside the synthesised `.call` body for G3. The emitter
    # (G4) and the liveness pass (G7) will revisit local promotion
    # once the actual state-machine codegen needs to preserve
    # cross-suspension locals as struct fields.
    param_names = list(func.parameters.keys())
    promoted = set(param_names)

    # 5. build the synthesized class body
    class_fields: Dict[str, zast.Node] = {}
    # The state cursor is i64 (matches the default integer literal
    # type, so the `state: 0` argument in the synthesised create
    # body type-checks without an explicit cast).
    state_path = AtomId(name="i64", start=func.start, synth_origin=_SYNTH_ORIGIN)
    class_fields["state"] = state_path
    # Per-parameter field: stored with the same ownership annotation
    # the parameter carried. Methods' :this.lock / :this.private.lock
    # become receiver-lock fields named after the type.
    for pname, ppath in func.parameters.items():
        field_name, field_path = _param_field(pname, ppath, receiver_type)
        if field_name is None or field_path is None:
            continue
        class_fields[field_name] = field_path

    # 6. build the `create` method
    create_method = _build_create_method(
        func.parameters, class_fields, func.start, receiver_type
    )

    # 7. build the `call` method (body rewritten in terms of `this`)
    call_method = _build_call_method(func.body, call_return, promoted, func.start)

    # 8. assemble the class
    class_as_items: Dict[str, zast.Node] = {
        "create": create_method,
        "call": call_method,
    }
    synth_class = ObjectDef(
        nodetype=NodeType.CLASS,
        is_items=class_fields,
        as_items=class_as_items,
        is_native=False,
        start=func.start,
        synth_origin=_SYNTH_ORIGIN,
    )

    # 9. build the factory: same params, body = `return synth.create ...`
    factory = _build_factory(synth_name, func, receiver_type)
    return synth_class, factory


def _param_field(
    pname: str, ppath: Path, receiver_type: Optional[str]
) -> Tuple[Optional[str], Optional[Path]]:
    """Translate one generator parameter into a class field name and
    type path. The factory's `meta.create` call uses this name when
    passing the parameter through."""
    if _is_this_path(ppath) or _is_this_private_path(ppath):
        # bare `:this` — rejected by _validate_parameter, shouldn't
        # reach here in a valid generator. Skip defensively.
        return None, None

    base, leaf = _split_path_leaf(ppath)

    # :this.lock / :this.private.lock: stash the receiver as a lock
    # field named after the type (matches the manual-iterator
    # pattern, e.g. `target: Bag.private.lock` in listiter.z).
    if _is_this_path(base) or _is_this_private_path(base):
        if receiver_type is None:
            return None, None
        # Reconstruct the field type as <Type>.lock or <Type>.private.lock.
        type_atom = AtomId(
            name=receiver_type, start=ppath.start, synth_origin=_SYNTH_ORIGIN
        )
        if _is_this_private_path(base):
            priv_atom = AtomId(
                name="private", start=ppath.start, synth_origin=_SYNTH_ORIGIN
            )
            base_path: Path = DottedPath(
                parent=type_atom,
                child=priv_atom,
                start=ppath.start,
                synth_origin=_SYNTH_ORIGIN,
            )
        else:
            base_path = type_atom
        lock_atom = AtomId(name="lock", start=ppath.start, synth_origin=_SYNTH_ORIGIN)
        field_path = DottedPath(
            parent=base_path,
            child=lock_atom,
            start=ppath.start,
            synth_origin=_SYNTH_ORIGIN,
        )
        return pname, field_path

    # Ordinary param: field stores the captured value with bare
    # type (no `.take` suffix — fields hold owned values directly;
    # the ownership transfer happens at the create call site).
    # `.lock` parameters keep the `.lock` suffix on the field so the
    # class holds the lock for its lifetime.
    if leaf == "take":  # ztc-string-compare-ok: ownership-suffix marker
        return pname, cast(Path, base)
    return pname, ppath


def _build_create_method(
    params: Dict[str, Path],
    class_fields: Dict[str, zast.Node],
    loc: Token,
    receiver_type: Optional[str],
) -> Function:
    """Synthesise the class's `create` method:

        create: function {<params>} out this is {
            return meta.create state: 0 :p1 :p2 ... <local-defaults>
        }

    Local fields are initialised to `Any.none` placeholders for v1;
    they'll be overwritten when the user's body first assigns them.
    """
    # Build the body: `meta.create state: 0 :p1 :p2 ... <locals>: default`
    meta_create = DottedPath(
        parent=AtomId(name="meta", start=loc, synth_origin=_SYNTH_ORIGIN),
        child=AtomId(name="create", start=loc, synth_origin=_SYNTH_ORIGIN),
        start=loc,
        synth_origin=_SYNTH_ORIGIN,
    )
    args: List[NamedOperation] = []
    # state field always starts at 0
    args.append(
        NamedOperation(
            name="state",
            valtype=AtomId(name="0", start=loc, synth_origin=_SYNTH_ORIGIN),
            start=loc,
            synth_origin=_SYNTH_ORIGIN,
        )
    )
    # forward each parameter as :name (label-value shorthand)
    for pname in params.keys():
        lv = zast.LabelValue(name=pname, start=loc, synth_origin=_SYNTH_ORIGIN)
        args.append(
            NamedOperation(
                name=pname, valtype=lv, start=loc, synth_origin=_SYNTH_ORIGIN
            )
        )
    create_call = Call(
        callable=meta_create,
        arguments=args,
        start=loc,
        synth_origin=_SYNTH_ORIGIN,
    )
    create_expr = Expression(
        expression=create_call, start=loc, synth_origin=_SYNTH_ORIGIN
    )
    # body: `{ meta.create ... }` — single expression statement
    line = StatementLine(
        statementline=create_expr, start=loc, synth_origin=_SYNTH_ORIGIN
    )
    body = Statement(statements=[line], start=loc, synth_origin=_SYNTH_ORIGIN)

    this_return = AtomId(name="this", start=loc, synth_origin=_SYNTH_ORIGIN)
    # Clone the param paths so they're independent from the factory's.
    create_params: Dict[str, Path] = dict(params)
    return Function(
        returntype=this_return,
        parameters=create_params,
        body=body,
        is_native=False,
        as_items={},
        start=loc,
        synth_origin=_SYNTH_ORIGIN,
    )


def _build_call_method(
    original_body: Statement,
    call_return: Call,
    promoted: Set[str],
    loc: Token,
) -> Function:
    """Build the synthesised `.call` method.

    Body is the original body with every reference to a promoted
    name (parameter or assigned local) rewritten as `this.<name>`.
    The Function carries `is_generator_call_body = True` (stored on
    its `synth_origin` as a discriminator) so the emitter can route
    it through the state-machine codegen in G4.

    For now we attach the marker via the function's `synth_origin`
    string: `"generator-call"`. A dedicated boolean on Function is
    nicer but Function is frozen and adding a field is a wider
    AST churn — pinning the discriminator on `synth_origin` keeps
    G3 self-contained.
    """
    rewritten = cast(Statement, _rewrite_to_this(original_body, promoted))
    this_param: Dict[str, Path] = {
        "this": AtomId(name="this", start=loc, synth_origin=_SYNTH_ORIGIN)
    }
    call_return_expr = Expression(
        expression=call_return, start=loc, synth_origin=_SYNTH_ORIGIN
    )
    return Function(
        returntype=call_return_expr,
        parameters=this_param,
        body=rewritten,
        is_native=False,
        as_items={},
        start=loc,
        synth_origin="generator-call",
    )


def _build_factory(
    synth_name: str, func: Function, receiver_type: Optional[str]
) -> Function:
    """Rewrite the original generator function as a factory that
    forwards its parameters to the synthesized class's `.create`.

    The factory's signature mirrors the original generator's
    (parameters and parameter ownership) — the only thing that
    changes is the body, which now constructs a synth-class
    instance and returns it.
    """
    loc = func.start
    synth_atom = AtomId(name=synth_name, start=loc, synth_origin=_SYNTH_ORIGIN)
    # Body: `<synth>.create p1: p1 p2: p2 ...`
    create_path = DottedPath(
        parent=synth_atom,
        child=AtomId(name="create", start=loc, synth_origin=_SYNTH_ORIGIN),
        start=loc,
        synth_origin=_SYNTH_ORIGIN,
    )
    args: List[NamedOperation] = []
    for pname, ppath in func.parameters.items():
        if _is_this_path(ppath) or _is_this_private_path(ppath):
            # bare receiver — shouldn't survive validation, skip.
            continue
        base, leaf = _split_path_leaf(ppath)
        if _is_this_path(base) or _is_this_private_path(base):
            # Forward the receiver — the param name on the function
            # came from `:this` / `:this.private`, so on the call
            # side we forward `this` directly under the same
            # synth-class field name (which is `pname`).
            valtype: zast.Operation = AtomId(
                name="this", start=loc, synth_origin=_SYNTH_ORIGIN
            )
        else:
            valtype = AtomId(name=pname, start=loc, synth_origin=_SYNTH_ORIGIN)
        args.append(
            NamedOperation(
                name=pname, valtype=valtype, start=loc, synth_origin=_SYNTH_ORIGIN
            )
        )
    factory_call = Call(
        callable=create_path,
        arguments=args,
        start=loc,
        synth_origin=_SYNTH_ORIGIN,
    )
    body_expr = Expression(
        expression=factory_call, start=loc, synth_origin=_SYNTH_ORIGIN
    )
    body_line = StatementLine(
        statementline=body_expr, start=loc, synth_origin=_SYNTH_ORIGIN
    )
    factory_body = Statement(
        statements=[body_line], start=loc, synth_origin=_SYNTH_ORIGIN
    )
    # The factory's return type is the synthesized class name.
    new_returntype: Path = AtomId(
        name=synth_name, start=loc, synth_origin=_SYNTH_ORIGIN
    )
    new_func = Function(
        returntype=new_returntype,
        parameters=dict(func.parameters),
        body=factory_body,
        is_native=False,
        as_items=dict(func.as_items),
        start=loc,
        synth_origin=_SYNTH_ORIGIN,
    )
    return new_func
