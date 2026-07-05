"""Per-face witness tally for the --thir-codegen non-vacuity report.

The corpus byte-diff proves routed bodies emit byte-identical C++, but says
nothing about a face (a gate arm / a lowering render) that NO corpus case
reaches -- a latent bug there stays invisible until its first witness
arrives. The test harness folds these counts across cases and xdist workers
(like the routed-body tally) and reports registered faces with zero
witnesses over the whole corpus run.

Witness semantics differ by face kind (encoded in the registry comment):
lowering faces record at THIR-node construction (the render actually
fired); the `own.*` gate rows record at gate ADMISSION -- their render is
the bare arg shared with the pass-through emit, so admission is the only
distinguishing site (an admit in a body later rejected elsewhere still
counts, a deliberate over-approximation); `flush.*` record when a flushable
statement position's lowered value actually carries a hoisted arg temp.

The registry is immutable metadata (module-level by design); the mutable
counts live on the active Compiler (`_thir_face_witnesses`), so the helper
is a no-op outside a compilation and the default (non---thir-codegen) path
never reaches it at all -- lowering and gating only run under the flag.
"""

from __future__ import annotations

from ..compilation_context import get_current_compiler

THIR_FACES: frozenset[str] = frozenset({
    # THIRArgTemp arms (lowering; _lower_call_arg / the method-arg row).
    "argtemp.value_union",          # free-call value-union member temp
    "argtemp.value_union_method",   # method-call value-union member temp
    "argtemp.record_rvalue",        # record-ctor rvalue into a ref slot
    "argtemp.own_copy",             # Own-slot copy+move `__tmp_N` temp
    # The temp-free last-use move (lowering).
    "move.own_last_use",            # `f(std::move(name))`
    # Pointer-repr Optional[record] slot faces (lowering).
    "optptr.none",                  # `nullptr`
    "optptr.ctor_rvalue",           # `&(__tmp_N)` addr-of arg temp
    "optptr.lift",                  # `::tpy::optional_to_ptr(...)`
    "optptr.pass",                  # already-`T*` binding passes bare
    "optptr.name",                  # `&(name)`
    # Pointer-variant union-slot lifts (lowering).
    "unionlift.none",               # `pv{std::monostate{}}`
    "unionlift.const_wrap",         # `ptr_variant_to_const(...)`
    "unionlift.member",             # `pv{&(name)}`
    # Own-cascade bare rows + the readonly ctor tail (gate admission).
    "own.scalar_rvalue",            # rvalue scalar into Own[scalar]
    "own.record_rvalue",            # record rvalue call into Own[record]
    "own.union_ctor",               # record-ctor rvalue into Own[union]
    "own.readonly_ctor",            # record-ctor rvalue into readonly slot
    # Self receiver / ctor-call renders (lowering).
    "self.this",                    # `self` name read -> `this`
    "call.self_method",             # `self.helper()` -> `this->helper()`
    "ctor.call",                    # THIRCtorCall bare ctor expansion
    # Runtime-BigInt `.to_fixed_check<T>()` narrows (lowering; the AST's
    # gen_index_expr / _gen_slice_bound / aug-assign / enum-from_value wraps).
    "narrow.subscript_index",       # `i.to_fixed_check<int32_t>()` (reads + del)
    "narrow.slice_bound",           # same wrap on a str/bytes slice bound
    "narrow.aug_value",             # `({0}).to_fixed_check<T>()` aug-assign value
    "narrow.enum_arg",              # `({0}).to_fixed_check<U>()` E(x) arg
    # BigInt-counter range loop (gate admission; the render difference is
    # the `::tpy::BigInt` cpp_elem + literal-bound retype, shared with the
    # fixed-int emit).
    "range.bigint_counter",
    # Enum value-binding renders (lowering).
    "enum.truthy_plain",            # plain-enum truthiness -> literal `true`
    "enum.truthy_int",              # IntEnum truthiness `(static_cast<U>(x) != 0)`
    "enum.neg",                     # IntEnum `-x` -> `(-static_cast<U>(x))`
    "enum.value",                   # `.value` -> `static_cast<U>(x)`
    "enum.name",                    # `.name` -> `EnumUtil<E>::name(x)` (BORROW)
    "enum.repr_print",              # @native enum print arg -> `::tpy::__repr__`
    "enum.nested_from_value",       # `Outer.Kind(v)` EnumUtil from_value
    # Sync `with` faces (lowering, per item / per statement).
    "with.manager_borrowed",        # lvalue manager: `auto& __ctx_N = ...`
    "with.manager_owned",           # rvalue manager: `auto __ctx_N = ...`
    "with.manager_deref",           # pointer-local manager: `*(...)` deref
    "with.as_value",                # `auto <name> = __enter__();`
    "with.as_ref",                  # `auto& <name> = __enter__();`
    "with.no_target",               # bare `__ctx_N.__enter__();`
    "with.suppress",                # bool __exit__: `if (!...) throw;` catch
    "with.exc_val",                 # `&__exc_N` passed to __exit__
    "with.cleanup_only",            # elided BaseException catch (common shape)
    "with.multi",                   # multiple managers in one statement
    # Sync `try` faces (lowering, per statement).
    "try.finally_only",             # the unified try/catch(...)/finally shape
    "try.throw_tier",               # C++ try/catch over the handler arms
    "try.multi_handler",            # 2+ catch arms
    "try.bare_except",              # `except:` -> `catch (...)`
    "try.binding",                  # `as e` -> the catch parameter
    "try.else",                     # goto __after_else_N past the handlers
    "try.except_finally",           # throw tier wrapped in the finally frame
    "try.hoist_decl",               # sema-hoisted plain-value predecls
    "try.body_terminates",          # normal-path finally copy elided
    "try.finally_terminates",       # raise/return-ending finally: no rethrow
    # Raise statements (lowering).
    "raise.ctor",                   # `raise X(args)` -> `throw <cpp>(...)`
    "raise.bare",                   # bare re-raise -> `throw;`
    # THIRMatch M1 -- the unguarded scalar switch tiers (lowering).
    "match.switch_enum",            # switch over enum-member case labels
    "match.switch_primitive",       # switch over int-literal case labels
    "match.if_elif",                # unguarded `==` chain (M2)
    "match.if_elif_else",           # the chain's wildcard `} else {` arm
    "match.bind_copy",              # capture/as: `auto name = subject;`
    "match.bind_ref",               # capture/as: `auto& name = subject;`
    "match.bind_assign",            # pre-declared/hoisted: `name = subject;`
    "match.if_elif_guarded",        # standalone-if + goto __match_end_N (M3b)
    "match.guard_arm",              # a guarded arm's inner `if (guard)`
    "match.switch_guard_chain",     # in-switch guard chain (grouped entries)
    "match.default_goto",           # all-guarded group -> goto __match_default_N
    "match.switch_union",           # switch (subject.index()) over variant tags
    "match.union_alias",            # `auto& __case_i = [*]std::get<idx>(...)`
    "match.union_none_arm",         # `case None:` -> the monostate index
    "match.union_default",          # wildcard/capture -> `default:` in place
    "match.guarded_union",          # per-index guard groups + goto end (M4b)
    "match.or_labels",              # or-pattern -> stacked case labels
    "match.wildcard_default",       # `case _:` -> the `default:` block
    "match.synthetic_default",      # non-exhaustive: `default: break;`
    "match.unreachable_tail",       # exhaustive + terminating arms tail
    "match.hoist_decl",             # sema-hoisted plain-value predecls
    # Emit-side finally-frame walks (recorded at emission -- the chain is
    # structural, so lowering never sees it; a no-op outside a compilation).
    # The with/try prefix keys on the walked segment's frame arms, so a
    # mixed stack witnesses both.
    "with.finally_return",          # return in body: inline __exit__ chain
    "with.finally_loop_exit",       # break/continue in body: partial chain
    "try.finally_return",           # return in body: finally body re-emitted
    "try.finally_loop_exit",        # break/continue in body: partial chain
    "try.chain_terminated",         # terminating finally suppressed the exit
    "match.loop_break_goto",        # break escaping a switch: goto __loop_break_N
    # The five flushable statement positions, counted only when the
    # position's value actually hoists an arg temp.
    "flush.vardecl",
    "flush.assign",
    "flush.field_write",
    "flush.return",
    "flush.expr_stmt",
})


def witness(face: str) -> bool:
    """Record one hit of `face` on the active compiler; no-op (but still
    True) when no compilation is in flight. Returns True so gate arms can
    tack it onto their admission conjunction (`... and witness("own.x")`)
    without restructuring."""
    assert face in THIR_FACES, f"unregistered THIR face: {face}"
    compiler = get_current_compiler()
    if compiler is not None:
        w = compiler._thir_face_witnesses
        w[face] = w.get(face, 0) + 1
    return True
