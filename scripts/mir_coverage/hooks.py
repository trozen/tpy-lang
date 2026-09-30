"""Runtime wrappers over sema that record which body each loan event belongs to.

Sema keeps no per-body record of the loans it tracked, so the only way to tell
a loan-active body from an inactive one without editing the compiler is to
wrap the entry points that establish loan / lifetime state and attribute each
call to the function sema is analyzing at that moment. Never edits tpyc; only
ever installed in a measurement process (it changes global class attributes).

Event kinds:
- STATE events (loan-active): `borrow:<kind>`, `view_var:<family>`,
  `provenance:*`, `param_returned`, `loop_frame_hold`, `with_exit_hold`,
  `lambda_ref_capture`, and `frame_close` (which fires for every resumable,
  so the report excludes it from the loan-active test).
- CHECK events (`check:*`): a lifetime check ran; not loan activity by itself.
"""
import functools
import sys
from collections import Counter

# id(TpyFunction) -> (func, Counter). The function object is kept so a reused
# id() after garbage collection is detected rather than misattributed.
EVENTS: dict[int, tuple[object, Counter]] = {}
# module name -> Counter, for events raised while analyzing top-level code.
INIT_EVENTS: dict[str, Counter] = {}
UNATTRIBUTED: Counter = Counter()

_SemanticContext = None
_TpyFunction = None
_installed = False


def reset() -> None:
    EVENTS.clear()
    INIT_EVENTS.clear()
    UNATTRIBUTED.clear()


def _record(func, kind: str, ctx=None) -> None:
    if func is None:
        UNATTRIBUTED[kind] += 1
        return
    if not isinstance(func, _TpyFunction):
        # Module-init sentinel: attribute to the module being analyzed.
        ctx = ctx or _find_ctx(2)
        mod = ctx.module_name if ctx is not None else "?"
        INIT_EVENTS.setdefault(mod, Counter())[kind] += 1
        return
    entry = EVENTS.get(id(func))
    if entry is None or entry[0] is not func:
        entry = (func, Counter())
        EVENTS[id(func)] = entry
    entry[1][kind] += 1


def _find_ctx(depth_start: int = 2):
    # Some wrapped entry points do not receive the SemanticContext; recover it
    # from the nearest caller frame that holds one.
    f = sys._getframe(depth_start)
    n = 0
    while f is not None and n < 40:
        loc = f.f_locals
        for nm in ("ctx", "self"):
            v = loc.get(nm)
            if v is None:
                continue
            if isinstance(v, _SemanticContext):
                return v
            c = getattr(v, "ctx", None)
            if isinstance(c, _SemanticContext):
                return c
        f = f.f_back
        n += 1
    return None


def _cur_from_ctx(ctx):
    return None if ctx is None else ctx.func.current_function


def _patch_everywhere(orig, wrapper) -> int:
    # Free functions are imported by name into several sema modules; rebinding
    # only the defining module would miss those call sites.
    hits = 0
    for name, mod in list(sys.modules.items()):
        if not name.startswith("tpyc") or mod is None:
            continue
        for attr, val in list(vars(mod).items()):
            if val is orig:
                setattr(mod, attr, wrapper)
                hits += 1
    return hits


def install() -> None:
    global _SemanticContext, _TpyFunction, _installed
    if _installed:
        return
    _installed = True
    import tpyc.compiler  # noqa: F401  -- the whole pipeline must be imported before patching
    from tpyc.parse.nodes import TpyFunction
    from tpyc.sema import context as sctx
    from tpyc.sema import (alias_rebind, calls, compatibility, expressions, frame_close,
                           loop_frames, receiver_calls)
    _TpyFunction = TpyFunction
    _SemanticContext = sctx.SemanticContext

    # --- state events ---
    orig_add = sctx.BorrowTracker.add_borrow

    @functools.wraps(orig_add)
    def add_borrow(self, storage, borrower, kind=sctx.BorrowKind.ALIAS, **kw):
        _record(_cur_from_ctx(_find_ctx()), f"borrow:{kind.value}" + (":elem" if kw.get("on_element") else ""))
        return orig_add(self, storage, borrower, kind, **kw)
    sctx.BorrowTracker.add_borrow = add_borrow

    orig_vv = sctx.SemanticContext.next_view_var_id

    @functools.wraps(orig_vv)
    def next_view_var_id(self, family):
        fam = "str" if family.pending_type_class is sctx.PendingStrType else "bytes"
        _record(self.func.current_function, f"view_var:{fam}")
        return orig_vv(self, family)
    sctx.SemanticContext.next_view_var_id = next_view_var_id

    fts = sctx.FunctionTrackingState
    orig_pd = fts.bp_set_param_derived

    def bp_set_param_derived(self, name, value):
        if value:
            _record(self.current_function, "provenance:param_derived")
        return orig_pd(self, name, value)
    fts.bp_set_param_derived = bp_set_param_derived

    orig_lv = fts.bp_add_loop_var_provenance

    def bp_add_loop_var_provenance(self, name):
        _record(self.current_function, "provenance:loop_var")
        return orig_lv(self, name)
    fts.bp_add_loop_var_provenance = bp_add_loop_var_provenance

    orig_pr = sctx.SemanticContext.mark_param_returned

    def mark_param_returned(self, name, _seen=None):
        # Only the outermost call: the method recurses through aliases.
        if _seen is None:
            _record(self.func.current_function, "param_returned")
        return orig_pr(self, name, _seen)
    sctx.SemanticContext.mark_param_returned = mark_param_returned

    orig_clh = loop_frames.check_loop_hold

    def check_loop_hold(ctx, hold):
        _record(ctx.func.current_function, "loop_frame_hold")
        return orig_clh(ctx, hold)
    _patch_everywhere(orig_clh, check_loop_hold)

    orig_wec = loop_frames.WithExitCheck

    def with_exit_check(*a, **kw):
        _record(_cur_from_ctx(_find_ctx()), "with_exit_hold")
        return orig_wec(*a, **kw)
    alias_rebind.WithExitCheck = with_exit_check

    orig_scm = frame_close.stamp_close_materials

    def stamp_close_materials(ctx, func, fi):
        _record(func, "frame_close")
        return orig_scm(ctx, func, fi)
    _patch_everywhere(orig_scm, stamp_close_materials)

    orig_ql = expressions.ExpressionAnalyzer._queue_lambda_borrow_check

    def _queue_lambda_borrow_check(self, expr, result):
        from tpyc.typesys import RefType
        if isinstance(result, RefType):
            _record(self.ctx.func.current_function, "lambda_ref_capture")
        return orig_ql(self, expr, result)
    expressions.ExpressionAnalyzer._queue_lambda_borrow_check = _queue_lambda_borrow_check

    # --- check events ---
    def make(orig, kind):
        @functools.wraps(orig)
        def w(self, *a, **kw):
            _record(_cur_from_ctx(getattr(self, "ctx", None) or _find_ctx()), kind)
            return orig(self, *a, **kw)
        return w

    for cls, meth, kind in (
            (compatibility.TypeCompatibility, "check_dangling_reference", "check:return_escape"),
            (compatibility.TypeCompatibility, "check_view_return_dangle", "check:view_return_escape"),
    ):
        setattr(cls, meth, make(getattr(cls, meth), kind))

    orig_bac = calls.CallAnalyzer._check_borrow_arg_conflicts

    def _check_borrow_arg_conflicts(self, expr):
        if self.ctx.func.borrow_tracker.loans:
            _record(self.ctx.func.current_function, "check:arg_conflict_with_live_loans")
        return orig_bac(self, expr)
    calls.CallAnalyzer._check_borrow_arg_conflicts = _check_borrow_arg_conflicts

    orig_rcl = receiver_calls.check_receiver_call_loans

    def check_receiver_call_loans(ctx, recv, *a, **kw):
        if ctx.func.borrow_tracker.loans:
            _record(ctx.func.current_function, "check:receiver_mutation_with_live_loans")
        return orig_rcl(ctx, recv, *a, **kw)
    _patch_everywhere(orig_rcl, check_receiver_call_loans)

    orig_mvb = sctx.SemanticContext.mark_view_borrowers_mutated

    def mark_view_borrowers_mutated(self, storage, family):
        if self.view_source_borrows_map(family):
            _record(self.func.current_function, "check:view_source_mutation")
        return orig_mvb(self, storage, family)
    sctx.SemanticContext.mark_view_borrowers_mutated = mark_view_borrowers_mutated
