"""What destroying a started generator or coroutine frame may run.

One fact per generator / async function,
`FunctionInfo.frame_close_runs_user_code`: destroying a started frame may
run user code when the body has a `finally` or `with` around a suspension
point, or when the frame may hold, across a suspension, a value whose
destruction may -- a local, an owned parameter, the source of a loop that
suspends, an awaited coroutine (`TpyType.drop_runs_user_code`, which reads
this fact back for a held generator or coroutine).

The body's materials are stamped when the body is analyzed. A body whose
answer is no even with every undecided fact read as yes is decided then;
the rest once every body of the module is analyzed, as a least fixed point
(a generator holding a frame of itself adds nothing). Until then, and for
a function whose materials are unknown, the answer is yes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..parse import TpyForEach, TpyNestedDef, TpyStmt, TpyTry, TpyWith
from ..parse.nodes import stmt_has_any_suspension, stmts_have_any_suspension
from ..typesys import OwnType, TypeParamRef, unwrap_ref_type

if TYPE_CHECKING:
    from ..parse import TpyFunction
    from ..typesys import FunctionInfo, TpyType
    from .context import SemanticContext


def stamp_close_materials(ctx: 'SemanticContext', func: 'TpyFunction',
                          fi: 'FunctionInfo') -> None:
    """Record, at the end of a generator or async body's analysis, what the
    close fact reads besides the frame locals, and queue the function for
    the module-end decision."""
    sources: list['TpyType | None'] = []
    fi.frame_cleanup_at_suspension = _walk(ctx, func.body, sources)
    fi.frame_loop_source_types = tuple(sources)
    # A no that holds with every open fact read as yes is final already.
    if not _close_runs_user_code(fi):
        fi.frame_close_runs_user_code = False
    else:
        ctx.frame_close_fis.append(fi)


def _walk(ctx: 'SemanticContext', stmts: list[TpyStmt],
          sources: list['TpyType | None']) -> bool:
    cleanup = False
    for s in stmts:
        if isinstance(s, TpyNestedDef):
            continue
        if isinstance(s, TpyTry) and s.finally_body and (
                stmts_have_any_suspension(s.try_body)
                or stmts_have_any_suspension(s.else_body)
                or any(stmts_have_any_suspension(h.body)
                       for h in s.handlers)):
            cleanup = True
        if isinstance(s, TpyWith) and stmts_have_any_suspension(s.body):
            cleanup = True
        if isinstance(s, TpyForEach) and stmt_has_any_suspension(s):
            sources.append(ctx.get_expr_type(s.iterable))
        for body in s.sub_bodies():
            cleanup = _walk(ctx, body, sources) or cleanup
    return cleanup


def _owned_param_type(t: 'TpyType') -> 'TpyType | None':
    """What a frame owns of a parameter of type `t`: a moved-in `Own[T]` or
    a value; a reference type it only borrows."""
    t = unwrap_ref_type(t)
    if isinstance(t, OwnType):
        return t.wrapped
    if t.is_value_type() or isinstance(t, TypeParamRef):
        return t
    return None


def _close_runs_user_code(fi: 'FunctionInfo') -> bool:
    if fi.frame_locals is None:
        return True
    if fi.frame_cleanup_at_suspension:
        return True
    held: list['TpyType | None'] = [t for _, t in fi.frame_locals]
    held += fi.frame_loop_source_types
    for p in fi.params:
        owned = _owned_param_type(p.type)
        if owned is not None:
            held.append(owned)
    if any(t is None or unwrap_ref_type(t).drop_runs_user_code()
           for t in held):
        return True
    return any(sub is None or sub.root.frame_close_runs_user_code is not False
               for sub in fi.frame_subframes or ())


def decide_frame_close(fis: 'list[FunctionInfo]') -> None:
    """Decide the fact for every generator and coroutine of a module, once
    all its bodies are analyzed: start from no, and turn a function yes when
    what it holds says so, until nothing changes."""
    pending = [fi for fi in fis if fi.frame_close_runs_user_code is None]
    for fi in pending:
        fi.frame_close_runs_user_code = False
    changed = True
    while changed:
        changed = False
        for fi in pending:
            if not fi.frame_close_runs_user_code and _close_runs_user_code(fi):
                fi.frame_close_runs_user_code = True
                changed = True
