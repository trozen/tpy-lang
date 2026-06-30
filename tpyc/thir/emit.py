"""THIR -> C++ emission for the value-scalar slice.

`emit_thir_body` writes a function body's C++ from THIR alone -- no
SemanticAnalyzer, no CodeGenContext. It reuses the existing analyzer-free leaf
helpers (`escape_cpp_name`, `expand_cpp_template`, `TpyType.to_cpp`) so its
output matches the AST-driven path byte-for-byte. As the slice grows this is
where the THIR codegen backend accretes.

Source comments are rendered through a `CommentSink` supplied by the codegen
seam (the stateless `ctx` comment helpers); the dump/tests pass the no-op
default so emission stays decoupled from the analyzer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TextIO

from ..codegen_cpp.context import INDENT, escape_cpp_name, expand_cpp_template
from ..codegen_cpp.forms import LocalBinding, is_plain_nonvalue
from ..typesys import OptionalType, unwrap_qualifiers
from .nodes import (
    Form,
    THIRAssign,
    THIRBinOp,
    THIRCall,
    THIRCoerce,
    THIRConstructor,
    THIRExpr,
    THIRFieldAccess,
    THIRForRange,
    THIRFormConvert,
    THIRFunction,
    THIRIf,
    THIRLiteral,
    THIRName,
    THIRNoOpStmt,
    THIRReturn,
    THIRSelf,
    THIRStmt,
    THIRVarDecl,
    THIRWhile,
)


class THIRCodeGenError(Exception):
    """A THIR node the slice's emitter does not handle reached emission.

    Lowering's eligibility gate should make this unreachable; it firing means
    the gate and the emitter disagree on the supported set.
    """


class CommentSink:
    """Renders the source comments the AST path emits before/around statements.

    The codegen seam supplies a subclass backed by the stateless `ctx` comment
    helpers; the no-op default keeps the dump/test paths analyzer-free.
    """

    def stmt(self, out: TextIO, loc, indent: str) -> None:
        ...

    def elif_(self, out: TextIO, loc, indent: str) -> None:
        ...

    def else_(self, out: TextIO, else_body, indent: str) -> None:
        ...

    def trailing(self, out: TextIO, body, indent: str) -> None:
        ...


_NO_COMMENTS = CommentSink()


@dataclass
class _EmitState:
    """Per-function emit state. `iter_counter` reproduces `ctx.iter_counter`:
    in the eligible slice only range-`for` loops bump it, and it resets per
    function, so a counter seeded at 0 here and bumped once per loop (pre-order)
    matches the AST path's `__start_N`/`__stop_N` numbering exactly.

    `slot_counter` reproduces `ctx.slots` for F2d rebind-slot pointer-locals:
    within the eligible slice only a REBIND_SLOT decl bumps it (every other
    `__slot_N` consumer -- unions, tuples, @dynamic, walrus -- is gated out), and
    it pre-increments per allocation just like `SlotState.next_slot`, so the
    `__slot_N` numbering matches the AST path. `rebind_slots` maps a rebind-slot
    local's name to its optional rebind slot N (allocated at the decl, read at
    each reseat) -- the analog of `ctx.rebind_slots`."""
    comments: CommentSink
    iter_counter: int = 0
    slot_counter: int = 0
    rebind_slots: dict[str, int] = field(default_factory=dict)

    def next_loop_index(self) -> int:
        n = self.iter_counter
        self.iter_counter += 1
        return n

    def next_slot(self) -> int:
        self.slot_counter += 1  # pre-increment: first slot is __slot_1
        return self.slot_counter


class CtxCommentSink(CommentSink):
    """CommentSink backed by a CodeGenContext's stateless comment helpers.

    Duck-typed on `ctx` so emit.py stays free of a CodeGenContext import.
    """

    def __init__(self, ctx):
        self._ctx = ctx

    def stmt(self, out: TextIO, loc, indent: str) -> None:
        self._ctx.emit_inline_comments(out, loc, indent)
        self._ctx.emit_source_comment(out, loc, indent)

    def elif_(self, out: TextIO, loc, indent: str) -> None:
        # An elif condition gets only its source line (the AST path emits no
        # inline comments for a flattened elif).
        self._ctx.emit_source_comment(out, loc, indent)

    def else_(self, out: TextIO, else_body, indent: str) -> None:
        self._ctx.emit_else_comment(out, else_body, indent)

    def trailing(self, out: TextIO, body, indent: str) -> None:
        self._ctx.emit_block_trailing_comments(out, body, indent)


# --- expressions ---


def _emit_literal(lit: THIRLiteral) -> str:
    v = lit.value
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        # Positional: a None into a storage-form Optional slot (field write /
        # storage-Optional return) is `std::nullopt`; a borrow/value-form None
        # (pointer-repr slot) is `nullptr`. The form is set by lowering.
        return "std::nullopt" if lit.form is Form.STORAGE else "nullptr"
    if isinstance(v, float):
        # Matches _gen_float_literal_value's double branch: repr() is the
        # shortest round-tripping form and a valid C++ double literal. Float32
        # (the `f`-suffixed branch) is excluded by the eligibility gate.
        return repr(v)
    return str(v)


def _emit_binop(e: THIRBinOp) -> str:
    # Mirrors ExpressionGenerator._gen_binop_from_result: apply the operand
    # wrappers, expand the operator's cpp_template, swap the checked div/mod
    # helper when the divisor is proven non-zero, and paren-wrap the result.
    # Comparisons reuse this path (their dunder carries a `{self} OP {0}`
    # template), so the same code emits both arithmetic and comparison binops.
    left, right = _emit_expr(e.left), _emit_expr(e.right)
    rb = e.resolved
    if rb is None:
        # Derived comparison (`<= > >= !=`): bare C++ operator, no template.
        return f"({left} {e.op} {right})" if e.paren_wrap else f"{left} {e.op} {right}"
    wl = rb.left_wrapper.replace("{self}", left).replace("{expr}", left)
    wr = rb.right_wrapper.replace("{self}", right).replace("{expr}", right)
    if rb.is_reverse:
        result = expand_cpp_template(rb.method.cpp_template, wr, wl)
    else:
        result = expand_cpp_template(rb.method.cpp_template, wl, wr)
    if e.divisor_non_zero:
        result = result.replace("div_check", "div_floor").replace("mod_check", "mod_floor")
    return f"({result})" if e.paren_wrap else result


def _emit_call(e: THIRCall) -> str:
    args = ", ".join(_emit_expr(a) for a in e.args)
    return f"{escape_cpp_name(e.callee)}({args})"


def _emit_field_access(e: THIRFieldAccess) -> str:
    return f"{_emit_expr(e.receiver)}{'->' if e.is_arrow else '.'}{e.field_cpp}"


def _emit_form_convert(e: THIRFormConvert) -> str:
    # storage->borrow lifts. optional_to_ptr's const overload is auto-selected by
    # the optional's own const-ness, so is_const here is carried for MIR / other
    # families, not the rendered helper. The borrow->storage direction (F2b) and
    # the union / tuple families arrive in later rungs.
    inner = _emit_expr(e.value)
    t = unwrap_qualifiers(e.result_type)
    if e.form is Form.BORROW:
        # F1 Optional[ref] read: `std::optional<T>` lvalue -> `T*`.
        if isinstance(t, OptionalType):
            return f"::tpy::optional_to_ptr({inner})"
        # F2a plain non-value lvalue -> reseatable `T*` pointer-local: address-of.
        if is_plain_nonvalue(t):
            return f"&({inner})"
    elif e.form is Form.STORAGE:
        # borrow `T*` -> storage `std::optional<T>` (write/return direction). An
        # owned source at last use moves (`ptr_to_optional_move`, F2e); a
        # non-owning borrow copies (`ptr_to_optional`, F2b/F2c). `move` is set by
        # lowering from the same `movable_locals` + last-use facts the AST reads.
        if isinstance(t, OptionalType):
            helper = "ptr_to_optional_move" if e.move else "ptr_to_optional"
            return f"::tpy::{helper}({inner})"
    raise THIRCodeGenError(
        f"unhandled THIRFormConvert: {type(t).__name__} {e.value.form}->{e.form}")


def _emit_expr(e: THIRExpr) -> str:
    if isinstance(e, THIRName):
        return escape_cpp_name(e.name)
    if isinstance(e, THIRSelf):
        return "this"
    if isinstance(e, THIRLiteral):
        return _emit_literal(e)
    if isinstance(e, THIRFieldAccess):
        return _emit_field_access(e)
    if isinstance(e, THIRFormConvert):
        return _emit_form_convert(e)
    if isinstance(e, THIRBinOp):
        return _emit_binop(e)
    if isinstance(e, THIRCall):
        return _emit_call(e)
    if isinstance(e, THIRCoerce):
        # int_literal_to_fixed_int is a passthrough -- the inner literal already
        # renders in the target type's context (a bare value).
        return _emit_expr(e.expr)
    raise THIRCodeGenError(f"unhandled THIR expr: {type(e).__name__}")


# --- statements ---


def _is_elif(outer: THIRIf, inner: THIRIf) -> bool:
    """Mirror StatementGenerator._is_elif: an `else_body` of a single THIRIf is
    a flattenable elif (vs a nested `else: if`) when their source columns match."""
    if outer.loc is None and inner.loc is None:
        return True
    if outer.loc is None or inner.loc is None:
        return False
    return inner.loc.column == outer.loc.column


def _emit_if(out: TextIO, stmt: THIRIf, indent_level: int, state: _EmitState) -> None:
    # The outer `// if ...:` comment is emitted by the caller (_emit_stmts).
    # Flatten the elif chain into `} else if (...)`, matching the AST path.
    indent = INDENT * indent_level
    body_indent = INDENT * (indent_level + 1)
    chain = [stmt]
    while (len(chain[-1].else_body) == 1
           and isinstance(chain[-1].else_body[0], THIRIf)
           and _is_elif(chain[-1], chain[-1].else_body[0])):
        chain.append(chain[-1].else_body[0])
    for i, node in enumerate(chain):
        if i == 0:
            out.write(f"{indent}if ({_emit_expr(node.condition)}) {{\n")
        else:
            state.comments.elif_(out, node.loc, indent)
            out.write(f"{indent}}} else if ({_emit_expr(node.condition)}) {{\n")
        _emit_stmts(out, node.then_body, indent_level + 1, state)
        state.comments.trailing(out, node.then_body, body_indent)
    last = chain[-1]
    if last.else_body:
        state.comments.else_(out, last.else_body, indent)
        out.write(f"{indent}}} else {{\n")
        _emit_stmts(out, last.else_body, indent_level + 1, state)
        state.comments.trailing(out, last.else_body, body_indent)
    out.write(f"{indent}}}\n")


def _emit_while(out: TextIO, stmt: THIRWhile, indent_level: int, state: _EmitState) -> None:
    # The `// while ...:` comment is emitted by the caller (_emit_stmts).
    indent = INDENT * indent_level
    out.write(f"{indent}while ({_emit_expr(stmt.condition)}) {{\n")
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.comments.trailing(out, stmt.body, INDENT * (indent_level + 1))
    out.write(f"{indent}}}\n")


def _emit_for_range(out: TextIO, stmt: THIRForRange, indent_level: int,
                    state: _EmitState) -> None:
    # Mirrors _gen_range_counter_loop (plus_one / non-hoisted branch): grab the
    # loop index BEFORE the body so nested loops number after this one (the AST
    # grabs `n` at the top of _gen_range_counter_loop). Non-literal bounds are
    # captured once into `__start_N`/`__stop_N` temps -- Python's range() reads
    # its args at call time, but the C++ condition re-reads each iteration.
    indent = INDENT * indent_level
    n = state.next_loop_index()
    cpp_elem = stmt.elem_type.to_cpp()
    var = escape_cpp_name(stmt.var)
    start_cpp = "0" if stmt.start is None else _emit_expr(stmt.start)
    stop_cpp = _emit_expr(stmt.stop)
    if stmt.start is not None and not stmt.start_is_literal:
        out.write(f"{indent}{cpp_elem} __start_{n} = {start_cpp};\n")
        start_cpp = f"__start_{n}"
    if not stmt.stop_is_literal:
        out.write(f"{indent}{cpp_elem} __stop_{n} = {stop_cpp};\n")
        stop_cpp = f"__stop_{n}"
    out.write(f"{indent}for ({cpp_elem} {var} = {start_cpp}; "
              f"{var} < {stop_cpp}; ++{var}) {{\n")
    _emit_stmts(out, stmt.body, indent_level + 1, state)
    state.comments.trailing(out, stmt.body, INDENT * (indent_level + 1))
    out.write(f"{indent}}}\n")


def _emit_stmt(out: TextIO, stmt: THIRStmt, indent_level: int, state: _EmitState) -> None:
    indent = INDENT * indent_level
    if isinstance(stmt, THIRVarDecl):
        name = escape_cpp_name(stmt.name)
        if stmt.cpp_local_representation is LocalBinding.REBIND_SLOT:
            # F2d two-slot rvalue pointer-local: a direct init slot holding the
            # value (so an alias taken before a reseat survives) + an empty
            # `std::optional<T>` rebind slot reused on each reseat. Mirrors
            # _gen_pointer_local_init's rvalue branch: the init slot is allocated
            # before the rebind slot.
            init_slot = state.next_slot()
            rebind_slot = state.next_slot()
            state.rebind_slots[stmt.name] = rebind_slot
            cpp = stmt.cpp_type
            const_pfx = "const " if stmt.is_const else ""
            out.write(f"{indent}{cpp} __slot_{init_slot} = {_emit_expr(stmt.init)};\n")
            out.write(f"{indent}std::optional<{cpp}> __slot_{rebind_slot};\n")
            out.write(f"{indent}{const_pfx}{cpp}* {name} = &__slot_{init_slot};\n")
        elif stmt.cpp_local_representation is not None:
            # Non-value borrow local. cpp_type is already the pointee record (the
            # optional's inner for OPTIONAL_TO_PTR, not the optional itself), so
            # the sigil alone distinguishes the `T&` alias from the `T*`.
            const_pfx = "const " if stmt.is_const else ""
            sigil = ("&" if stmt.cpp_local_representation is LocalBinding.REF_ALIAS
                     else "*")
            out.write(f"{indent}{const_pfx}{stmt.cpp_type}{sigil} {name} = "
                      f"{_emit_expr(stmt.init)};\n")
        elif stmt.init is None:
            out.write(f"{indent}{stmt.resolved_type.to_cpp()} {name};\n")
        else:
            cpp_type = stmt.resolved_type.to_cpp()
            out.write(f"{indent}{cpp_type} {name} = {_emit_expr(stmt.init)};\n")
    elif isinstance(stmt, THIRAssign):
        # target is a THIRName (`x = ...`) or, for F2b, a THIRFieldAccess
        # (`recv.field = ...` / `recv->field = ...`); _emit_expr renders both. An
        # F2d rebind-slot pointer-local reseat reuses its optional slot:
        # `p = &*(__slot_N = <rvalue>);`.
        if isinstance(stmt.target, THIRName) and stmt.target.name in state.rebind_slots:
            slot = state.rebind_slots[stmt.target.name]
            out.write(f"{indent}{escape_cpp_name(stmt.target.name)} = "
                      f"&*(__slot_{slot} = {_emit_expr(stmt.value)});\n")
        else:
            out.write(f"{indent}{_emit_expr(stmt.target)} = {_emit_expr(stmt.value)};\n")
    elif isinstance(stmt, THIRReturn):
        if stmt.value is None:
            out.write(f"{indent}return;\n")
        else:
            out.write(f"{indent}return {_emit_expr(stmt.value)};\n")
    elif isinstance(stmt, THIRIf):
        _emit_if(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRWhile):
        _emit_while(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRForRange):
        _emit_for_range(out, stmt, indent_level, state)
    elif isinstance(stmt, THIRNoOpStmt):
        # No code -- the `// pass` source comment (if any) is emitted by the
        # caller (_emit_stmts) from the node's loc.
        pass
    else:
        raise THIRCodeGenError(f"unhandled THIR stmt: {type(stmt).__name__}")


def _emit_stmts(out: TextIO, stmts, indent_level: int, state: _EmitState) -> None:
    indent = INDENT * indent_level
    for stmt in stmts:
        state.comments.stmt(out, stmt.loc, indent)
        _emit_stmt(out, stmt, indent_level, state)


def emit_thir_body(out: TextIO, fn: THIRFunction, indent_level: int = 1,
                   *, comments: CommentSink | None = None) -> None:
    """Emit `fn`'s body statements (no signature, no braces) at `indent_level`."""
    _emit_stmts(out, fn.body, indent_level, _EmitState(comments or _NO_COMMENTS))


def emit_thir_constructor_tail(out: TextIO, ctor: THIRConstructor,
                               *, comments: CommentSink | None = None) -> None:
    """Emit a constructor's member-init-list + body tail (the ` : f(v)... {}` that
    follows the signature). The THIR counterpart of gen_record_decl's AST MIL+body
    emit: the signature is written by the AST path before this is called (the M1
    precedent -- signatures stay on the AST path). Byte-identical to that path's
    tail. M3a is pure-MIL, so `body` is empty and this emits ` {}` (or
    ` : inits {}`)."""
    inits = [f"{bi.base_cpp}({', '.join(_emit_expr(a) for a in bi.args)})"
             for bi in ctor.base_inits]
    inits.extend(
        f"{mi.field_cpp}(std::move({_emit_expr(mi.value)}))" if mi.move
        else f"{mi.field_cpp}({_emit_expr(mi.value)})"
        for mi in ctor.mil_inits)
    if inits:
        out.write(" : ")
        out.write(", ".join(inits))
    if ctor.body:
        out.write(" {\n")
        _emit_stmts(out, ctor.body, 2, _EmitState(comments or _NO_COMMENTS))
        out.write(f"{INDENT}}}\n")
    else:
        out.write(" {}\n")
