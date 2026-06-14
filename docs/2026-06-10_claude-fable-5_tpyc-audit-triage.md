# tpyc Audit Triage -- 15 Root Causes Behind 242 Findings

Companion to `2026-06-10_claude-fable-5_tpyc-compiler-audit.md`. Ordered by
(severity x how idiomatic the triggering code is). "Blast radius" counts verified
findings collapsed by the fix; theme names refer to the full report.

## Status

Updated as fixes land. The full report is a point-in-time record and is not
edited; residual gaps from partial fixes are tracked in `BUGS.md`.

| # | Root cause | Status |
|---|------------|--------|
| 1 | Flow facts survive paths that kill them | **fixed** -- `fix-flow-fact-kill-sets` (kill-sets at loop/handler/finally meets); residuals in BUGS.md: codegen CFG kill gap, readonly back-edge rebind, finally definite-assignment |
| 2 | Narrowing not invalidated by calls/aliases | **fixed** -- same branch (closure-nonlocal kill at call sites, alias-group invalidation); residuals in BUGS.md: rebound-alias group, field-access call args, module globals |
| 3 | Optional-match non-None patterns as catch-alls | **fixed** -- B15/B16/B21 (per-side coverage in sema, None routed to wildcard/capture arms in codegen, CPython capture semantics); also fixed B17 (non-enumerable scalar exhaustiveness) and the non-exhaustive fall-through flow-state merge. Residuals in BUGS.md: no missing-return check (pre-existing, now more visible), Optional or-pattern as-binding |
| 4 | match arm-dispatch codegen structurally fragile | **fixed** -- G1 (break past switch), G8 (numbered subjects), B73 (byte-based string buckets, incl. enum try_parse sibling), B18/B19/B20 (guard lowering unified on bindings-first standalone-if + goto, incl. two latent siblings: dropped guards on optimized-Optional capture arms, string-switch guarded-prefix bind-after-guard), B22 (guarded-union case None), B23 (storage-form Optional subject lift). G2 (dangling arm bindings on subject mutation) split out per approval -- filed in BUGS.md Safety, needs the BorrowTracker place/loan design |
| 5 | Auto-move fires on alias-blind liveness | **fixed** -- `fix-auto-move-borrow-gate` (liveness walk fixes for B28/B32/B33, chain-alias suppression for B30+subscript, consume-site BorrowTracker gate for B29/B31); residuals in BUGS.md Safety section: cross-module-cycle borrowing callees (same-module forward refs fixed by `fix-forward-ref-borrow-gate`), nested-call-arg borrows (create_task shape) |
| 6 | `finally` is mis-lowered three ways | **fixed** -- `fix-finally-lowering` (G3: return value captured into a typed temp before the finally/`__exit__` chain, sync + async non-CFG + error_return propagate; G36: finally-body bindings hoisted like try-body bindings + per-copy local-scope snapshot; G10: per-function emission context isolated at nested-def boundaries in codegen + sema try/except state moved into FunctionTrackingState; G35: frame destructor runs pending helper finallies / `with.__exit__` on abandonment, `tpy::frame_state` neuters moved-from frames, temp-iterable for-loops brace-scoped for CPython drop timing, yield-in-finally warned + skipped at destruction, raising cleanup panics). Residuals in BUGS.md: nested def across suspension, async CFG-finally on uncancelled abandonment, break/continue across CFG-finally (B62) |
| 8 | Parser accepts-then-ignores real syntax | **fixed** -- posonly params fully supported (parse + default alignment + keyword-binding rejection incl. protocol methods; ctor enforcement lenient, see LANGUAGE_FEATURES); for-loop rebinds existing value-type locals (B47; reference-type rebind rejected pending the borrow-tracked binding design, type-changing rebind errors -- residuals in BUGS.md); assert-message awaits desugared conditional, match-guard awaits rejected (B128+B25); raise-from warns (B130); multi-assign left-to-right (B131); __all__ += supported, dynamic mutation loud; B129 loc slip. B127 (self.x: T = v field declarations) split out for /tpy-add-feature per approval; B46 (items()-unpack post-loop UB) tracked with cluster 7 |
| 7 | dict access paths return copies | **fixed** -- `fix-dict-access-aliasing`: items()/values() alias via proxy-ref iterators + @auto_readonly views (B51/B55, incl. generator/async G21 and post-loop B46 -- the unpack-hoist fix covers list-of-tuples too); setdefault returns a borrow (B52 half); two-arg get(k, default) stays a copy but now WARNS (copy() acknowledges) -- borrow form tracked in BUGS.md (conditional borrow + rvalue-default slot lifetime need design) |
| 12 | Callable/Fn values second-class in sema | **fixed** -- `fix-callable-values` (B95+B104: one shared pipeline `analyze_callable_value_call` for Fn/Callable params, locals, and fields -- args routed through `_typecheck_and_coerce_arg`, synthetic FunctionInfo non-readonly, reference args to non-readonly callable params eagerly marked mutated since a value-typed callee never gets Phase-2 facts; bare generic slots excluded to keep combinator const-ness; B97: coercions applied, not just checked; B101: params contravariant / returns covariant via `_callable_signature_satisfies`, shared with the record-`__call__` branch; B103: assert replaced by per-overload signature matching + located error, plus the previously-unreachable `operator()` emission for @overload `__call__` groups fixed. std::function / Fn requires params spell mutable by default; `readonly[...]` in the param list is the explicit const contract) |
| 9-11, 13-15 | -- | open |

---

**1. Flow facts survive paths that kill them** -- `init_tracker.apply_loop_entry_facts`, except/finally analyzed from pre-try / end-of-try state, no back-edge kill.
- *Blast*: ~10 findings (theme: flow-sensitive analysis), all silent UB -- narrowing, non-null Ptr, value-range bounds/div-zero elision, readonly rebinds. Trigger: any loop or try that reassigns a narrowed var.
- *Fix*: prescan each loop/try/finally body for assigned names (prescan already collects them) and kill those facts at body entry. No fixpoint needed; sound and cheap.

**2. Narrowing never invalidated by calls or aliases** -- `narrowing.py:764-808` invalidates field facts only for named args.
- *Blast*: 3 criticals (closure `nonlocal` writes, alias-rooted field facts, global roots) -- segfault repros on 10-line programs.
- *Fix*: on any call to a local closure/callable value, kill facts for names it writes (sema records nonlocal/global); kill field facts rooted at any alias of the receiver, not just the receiver name.

**3. Optional-subject match treats non-None patterns as catch-alls** -- `sema/match.py:263-283` + codegen partition under `has_value()`.
- *Blast*: 5 findings, critical: `case _:`/`case x:`/`case C():` never match None, exhaustiveness then emits `std::unreachable()` (stack smashing observed); also bogus unreachable-arm rejections.
- *Fix*: on Optional subjects only wildcard/capture are irrefutable (and they must cover None); codegen emits an else branch for the None path; `_match_missing_cases` must report the missing non-None side too.

**4. match arm-dispatch codegen is structurally fragile** -- switch lowering + if/elif chain surgery.
- *Blast*: ~8 findings: `break` exits the C++ switch not the loop (critical); guarded capture arms sever the else-chain (two arms execute) or swallow later arms; `__match_subject` redeclared in same scope; string-switch discriminator computed on code points but switched on UTF-8 bytes; arm bindings dangle if the body mutates the subject.
- *Fix*: route `break`/`continue` through loop labels (flag or goto), keep guard failures inside the chain, number and brace-scope subjects, compute discriminators on encoded bytes, copy-or-pin bindings when the arm writes the subject.

**5. Auto-move fires on alias-blind liveness** -- alias map is name-to-name only (`prescan.py:210-213`).
- *Blast*: ~9 findings, critical: field aliases (`a = o.inner`), call-result borrows (`n = first(xs)`), generator objects borrowing their iterable, closures capturing by ref after the def site, same-statement target/value splits -- each is a silent use-after-move/UAF.
- *Fix (near-term)*: gate auto-move on *provable absence of any borrow* (any field access, borrowing call, nested def, or with/generator involvement of the var disables it). Real fix is MIR places/loans, already planned.

**6. `finally` is mis-lowered three ways** -- `statements.py:3503` + gen frame emission + nested-def context leak.
- *Blast*: 4 findings, critical: finally (and `__exit__`) runs *before* the return expression is evaluated; abandoned generators never run pending finallys; nested defs inline the outer finally into the lambda / goto outer labels; vars first bound in finally emit invalid C++ (double emission shares declared_vars).
- *Fix*: evaluate return value into a temp before the finally chain; reset finally_stack/try labels at nested-def boundaries; run pending finallys from the frame destructor; snapshot declared_vars per finally copy.

**7. dict access paths return copies** -- `ordered_map`/`dict_ops` yield `V` by value; compiler emits them anyway.
- *Blast*: 4 findings: `for k, v in d.items()` mutations lost (critical-adjacent, fully silent), `d.get(k, default)` / `setdefault(k, []).append(x)` no-op, `d.values()` mutation is a raw C++ error.
- *Fix*: items iterator yields `std::tuple<const K&, V&>`, get/setdefault return `V&` (or the compiler binds through `find`), values view drops the const-only restriction; codegen already has the tuple_to_pointer machinery to consume references.

**8. Parser accepts-then-ignores real syntax** -- positional-only params, `raise..from`, assert messages, `__all__ +=`, multi-assign order, loop-var rebinding.
- *Blast*: ~7 findings; worst is posonly: `def f(a, /, b=5)` silently drops `a` and binds args to the wrong defaults (wrong values, zero diagnostics). `for x in ...` over an existing local leaves the pre-loop value after the loop.
- *Fix*: prepend `posonlyargs` and fix default alignment (small, mechanical); mark pre-declared for-vars as reassigned in prescan; for the rest, either implement or emit explicit "not supported" diagnostics -- silence is the bug.

**9. Borrow/storage conversion missed at second-tier boundaries** -- each emitter hand-rolls the conversion.
- *Blast*: ~12 findings (themes: borrow-vs-storage + gap sweep): walrus, ternary joins, container-element stores, match subjects from fields, await results, generic calls with union/Optional args, union field -> union param. Mostly C++ build errors on valid code; a few silent copies/moves-from-live-objects.
- *Fix*: one chokepoint helper `convert(expr, src_form, dst_form, type)` that every arg/assign/return/bind site must call; sema tags the source form on the node (THIR direction anyway). Patch the seven known sites through it first.

**10. View lifetime tracking has whole-class bypasses** -- pending-view machinery keyed on owned-family qnames only; `is_dangling_return` whitelist misses expression kinds.
- *Blast*: ~7 findings, critical: locals typed from StrView/BytesView-returning expressions get no tracking; `return a.strip()` / f-string returns escape as views of dead storage; `list[StrView].append(make())` stores a dangling view; `a += ...` doesn't fire the pinned-view warning reassignment does.
- *Fix*: classify by "does this expression yield a view" (TypeDef fact), not by qname family; make `is_dangling_return` recurse uniformly over exprs (method calls, f-strings); apply the documented-but-unimplemented StrView field source rules.

**11. Reference types silently copied at binding sinks** -- hoisted `std::optional<T>` bindings, literal elements, captures.
- *Blast*: ~8 findings: match captures and walrus of class subjects copy; set/list/dict literal elements copy with no warning (`.append` warns -- sibling inconsistency); `bytearray` classified `is_value_type=True`; escaping lambdas capture by value (already in BUGS.md).
- *Fix*: bind hoisted captures in pointer form (tuples already do); fire the existing storage-copy warning from literal-element and insert-method coercions; reclassify bytearray as a reference type.

**12. Callable/Fn values are second-class in sema** -- synthetic `FunctionInfo(is_readonly=True)`, no coercions, wrong variance.
- *Blast*: 5 findings: mutation/borrow safety checks suppressed for any callback call (UAF window), checked-but-unwrapped arg coercions (C++ errors), covariant param check (unsound accept + sound reject), `__call__` overload assert crash.
- *Fix*: treat callable values as opaque *mutating* callees, run the normal arg-coercion pipeline, flip param compatibility to contravariant, replace the assert with overload resolution.

**13. Identity by short name across modules** -- placeholder `NominalType`s without `_module_qname` reach identity-sensitive ops.
- *Blast*: 6 findings: same-named records from two modules collapse in unions, satisfy `record_to_ptr` coercions, unify during generic inference; `error_return` matches exceptions by bare name; union ordering ties broken by insertion order (ABI instability).
- *Fix*: qualify placeholders before `make_union`/coercion/inference (resolve_refs already knows the module); compare `qualified_name()` everywhere a `.name ==` exists (grep-able pattern); sort unions by qualified name.

**14. Pending container-literal resolution is fragile** -- expected-type context not propagated into nested literals; mutation detection name-keyed.
- *Blast*: ~10 findings: ICE on `{1: [3, 1, 2]}`, bogus rejections of nested literals and `xs, ys = ys, xs` (RecursionError), `std::array` locals appended into `vector<vector<T>>`, literal-width unification keeping only the first literal's value.
- *Fix*: push the annotation/param type into nested literal analysis (one recursive hint pass); mark mutation through subscript chains and method/ctor/`__call__` args, not just direct names; unify literal ranges, not first-wins.

**15. Front-end algorithmic cliffs** -- invisible on the test corpus, quadratic at scale.
- *Blast*: 6 findings: `InitTracker.save()` copies 13 dicts/frozensets of *all* per-function flow state 3x per if-statement; mutation-propagation cycle fallback re-sweeps a flat list per round; protocol conformance has no (type, protocol) memo; `_finalize_declarations` rebuilds ModuleInfo per dep edge (quadratic in workspace); str-keyed dict lookups allocate a `std::string` per probe at runtime.
- *Fix*: copy-on-write flow snapshots (or dirty-key journals); SCC + worklist for the fixpoint; conformance memo keyed by (type qname, protocol, args); cache `_exports_to_module_info` per module; transparent hash/equal on `ordered_map`.

---

**Sequencing suggestion** (items 1-5 are done -- see Status): 8's posonly fix next (trivial, silent-wrong-values). 7, 9, 10 are each "build the chokepoint, then migrate sites" projects. 14 and 15 can ride along with the THIR migration. 6-item `finally` work and 12 are self-contained. Design decisions needed (not patches): Optional-of-tuple form (report theme: borrow-vs-storage), `a[:]` aliasing-vs-copy, byte-based string semantics documentation (G50).
