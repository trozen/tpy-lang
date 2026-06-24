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

from typing import TextIO

from ..codegen_cpp.context import INDENT, escape_cpp_name, expand_cpp_template
from .nodes import (
    THIRAssign,
    THIRBinOp,
    THIRCall,
    THIRCoerce,
    THIRExpr,
    THIRFunction,
    THIRIf,
    THIRLiteral,
    THIRName,
    THIRReturn,
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
        return "nullptr"
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
        return f"({left} {e.op} {right})"
    wl = rb.left_wrapper.replace("{self}", left).replace("{expr}", left)
    wr = rb.right_wrapper.replace("{self}", right).replace("{expr}", right)
    if rb.is_reverse:
        result = expand_cpp_template(rb.method.cpp_template, wr, wl)
    else:
        result = expand_cpp_template(rb.method.cpp_template, wl, wr)
    if e.divisor_non_zero:
        result = result.replace("div_check", "div_floor").replace("mod_check", "mod_floor")
    return f"({result})"


def _emit_call(e: THIRCall) -> str:
    args = ", ".join(_emit_expr(a) for a in e.args)
    return f"{escape_cpp_name(e.callee)}({args})"


def _emit_expr(e: THIRExpr) -> str:
    if isinstance(e, THIRName):
        return escape_cpp_name(e.name)
    if isinstance(e, THIRLiteral):
        return _emit_literal(e)
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


def _emit_if(out: TextIO, stmt: THIRIf, indent_level: int, comments: CommentSink) -> None:
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
            comments.elif_(out, node.loc, indent)
            out.write(f"{indent}}} else if ({_emit_expr(node.condition)}) {{\n")
        _emit_stmts(out, node.then_body, indent_level + 1, comments)
        comments.trailing(out, node.then_body, body_indent)
    last = chain[-1]
    if last.else_body:
        comments.else_(out, last.else_body, indent)
        out.write(f"{indent}}} else {{\n")
        _emit_stmts(out, last.else_body, indent_level + 1, comments)
        comments.trailing(out, last.else_body, body_indent)
    out.write(f"{indent}}}\n")


def _emit_while(out: TextIO, stmt: THIRWhile, indent_level: int, comments: CommentSink) -> None:
    # The `// while ...:` comment is emitted by the caller (_emit_stmts).
    indent = INDENT * indent_level
    out.write(f"{indent}while ({_emit_expr(stmt.condition)}) {{\n")
    _emit_stmts(out, stmt.body, indent_level + 1, comments)
    comments.trailing(out, stmt.body, INDENT * (indent_level + 1))
    out.write(f"{indent}}}\n")


def _emit_stmt(out: TextIO, stmt: THIRStmt, indent_level: int, comments: CommentSink) -> None:
    indent = INDENT * indent_level
    if isinstance(stmt, THIRVarDecl):
        cpp_type = stmt.resolved_type.to_cpp()
        name = escape_cpp_name(stmt.name)
        if stmt.init is None:
            out.write(f"{indent}{cpp_type} {name};\n")
        else:
            out.write(f"{indent}{cpp_type} {name} = {_emit_expr(stmt.init)};\n")
    elif isinstance(stmt, THIRAssign):
        out.write(f"{indent}{escape_cpp_name(stmt.target.name)} = {_emit_expr(stmt.value)};\n")
    elif isinstance(stmt, THIRReturn):
        if stmt.value is None:
            out.write(f"{indent}return;\n")
        else:
            out.write(f"{indent}return {_emit_expr(stmt.value)};\n")
    elif isinstance(stmt, THIRIf):
        _emit_if(out, stmt, indent_level, comments)
    elif isinstance(stmt, THIRWhile):
        _emit_while(out, stmt, indent_level, comments)
    else:
        raise THIRCodeGenError(f"unhandled THIR stmt: {type(stmt).__name__}")


def _emit_stmts(out: TextIO, stmts, indent_level: int, comments: CommentSink) -> None:
    indent = INDENT * indent_level
    for stmt in stmts:
        comments.stmt(out, stmt.loc, indent)
        _emit_stmt(out, stmt, indent_level, comments)


def emit_thir_body(out: TextIO, fn: THIRFunction, indent_level: int = 1,
                   *, comments: CommentSink | None = None) -> None:
    """Emit `fn`'s body statements (no signature, no braces) at `indent_level`."""
    _emit_stmts(out, fn.body, indent_level, comments or _NO_COMMENTS)
