# TurboPython Compiler Audit

- **Auditor**: Claude Fable 5 (`claude-fable-5`), multi-agent workflow (Claude Code)
- **Date**: 2026-06-10 (started 16:58 CEST, completed 22:46 CEST; interrupted twice by session rate limits and resumed with cached agent results)
- **Scope**: the `tpyc/` compiler front-end and codegen, with `docs/` and `lib/tpy/tpy/` stubs as reference. The C++ runtime, stdlib breadth (`tplib/`, stdlib modules), and `lib/cpy/` are out of scope, except where the compiler emits code whose correctness depends on them (a handful of findings cite `runtime/cpp/include/tpy/dict_ops.hpp` / `ordered_map.hpp` because the compiler silently emits calls with CPython-divergent semantics; the *decision to emit them* is the compiler issue).
- **Read-only audit**: no repository code was modified. All repro snippets live under `/tmp/agents/`.

## 1. Methodology

Four-phase adversarial workflow, ~80 effective subagents (finders, verifiers, gap-sweepers; ~14.5M subagent tokens across the runs):

1. **Review (20 agents)** -- 13 module deep-dives covering every file in `tpyc/` (parser+frontend-IR, type system, sema core/registration, flow analysis, expressions, calls/overloads/compatibility, methods/protocols/match, codegen expressions, codegen statements/match, codegen functions/records, generators/async/resumable-CFG, orchestration/CLI/REPL, macros) plus 7 cross-cutting hunters (Own[T]/moves, borrow-vs-storage boundaries, silent copies vs CPython, dangling views, readonly+narrowing soundness, perf cliffs, slop). Each agent made two passes (read+map, then adversarial) and was required to reproduce suspected miscompiles with `--dump-code` / build+run snippets.
2. **Verify** -- every finding went to an adversarial verifier instructed to *refute* it against the actual code, re-running repros for miscompile/UB/silent-copy claims. Verdicts: confirmed / adjusted (real but description or severity corrected) / refuted / unverifiable.
3. **Gap sweep (6 agents)** -- a second full round seeded with the confirmed findings: sibling-construct tracing, follow-up on each round-1 agent's declared coverage gaps, and fresh-eyes passes on exceptions/`with`/`@error_return`, strings/bytes/f-strings, enums/unions/match, and cross-feature interactions in the two largest files (`codegen_cpp/expressions.py`, `sema/calls.py`).
4. **Verify 2** -- the same adversarial gate over gap-sweep findings.

**Round-1 outcome**: 195 findings filed, **1 refuted, 1 disputed** (see section 9), 194 survived (161 fully confirmed, 33 confirmed-with-adjustment); **165 of 194 carry an executed reproduction** (observed bad C++, wrong runtime output, crash, or bogus diagnostic), the rest are code-read evidence. Severity after verifier recalibration: **20 critical, 31 high, 92 medium, 51 low**. Categories: 57 rejects-valid, 30 miscompile, 15 UB, 16 unsound-safety, 13 crash, 11 silent-copy, 24 design, 22 slop, 6 perf.

**Round-2 outcome**: 59 findings filed, all 59 survived verification (45 confirmed, 14 adjusted; 58 reproduced). Six are duplicates of round-1 entries or of each other (independent cross-confirmation -- itself a useful signal) and are merged; **53 are new**, including 6 new criticals the first round missed entirely: `finally` (and `with` `__exit__`) running *before* the return expression is evaluated; `break` inside a switch-lowered match arm exiting the switch instead of the loop; match arm bindings left dangling when the arm body mutates the subject; two view-lifetime bypasses (StrView/BytesView locals skipping all lifetime tracking; view-returning *method calls* invisible to `is_dangling_return`); and the finally-axis sibling of the round-1 exception flow-fact hole. The gap sweep's hit rate vindicates the two-round design: most of these live exactly where round-1 reviewers declared bounded coverage, or on sibling axes of confirmed round-1 bugs.

**Total: 242 unique verified findings** (189 round-1 after duplicate merging + 53 round-2): **25 critical, 36 high, 123 medium, 58 low**.

**Calibration notes.** Severity follows the project's own scale (critical = silently emitted miscompile/UB on plausible code; CPython-parity divergences *with a workaround* cap at medium). Three honesty caveats: (a) the verification pass refuted only 1 of 254 findings outright -- finder precision was genuinely high (88% of surviving findings shipped with executed repros), but a sub-1% refutation rate also means the adversarial bar should be assumed softer than a hostile human reviewer; treat unreproduced (`code-read evidence`) entries as strong leads, not proven facts. (b) A rate-limit interruption forced a resume that re-ran some verification chunks; 18 of 195 round-1 verdicts came back with different severity calibrations (both directions) and one refutation flipped to adjusted-low. The final run is reported; the instability is a measure of +/-1 severity-step noise in the calibration, not of the facts (the factual claims were stable across runs). (c) Findings were *not* exhaustively cross-checked against `BUGS.md`; reviewers checked opportunistically and the borrow/storage hunter explicitly skipped already-tracked entries, but at least three reported findings overlap existing BUGS.md entries (noted in section 3 below). Line numbers reflect the tree at audit time (master @ 726fb9288 + uncommitted work).

## 2. Verdict on the language design (the divergences from Python)

The central question posed for this audit: is this the right design for a Python-syntax / Rust-semantics language, and is it internally consistent? Short answers, justified by the findings:

**The core ownership model is sound and the right call.** The pointer-local / value-storage split (locals alias like CPython; fields, globals and container elements own inline, with copy warnings at the boundary) is a defensible, internally consistent middle ground -- it keeps the hot 90% (local binding, param passing, element borrowing) allocation- and refcount-free while confining CPython divergence to persistence boundaries where a warning can be emitted. The rejected-alternatives analysis in `docs/OWNERSHIP_DESIGN.md` is honest and correct. The audit found *no* finding that invalidates the model itself; what it found is that the model's *enforcement machinery* (liveness/alias analysis for auto-move, flow facts for check elision, form conversion at boundaries) has implementation holes. The design intent of `SAFETY_MODEL.md` -- "compile-time where easy, runtime checks where hard, never force annotations" -- is consistently applied in sema; the problem is that several "compile-time where easy" checks are unsound in loops and exception paths (theme: flow-sensitive analysis), and several promised runtime backstops don't exist yet, which the docs do admit.

**The borrow-form/storage-form duality is the right compilation strategy but is under-engineered as implemented.** CLAUDE.md calls it a recurring bug class; the audit confirms that assessment quantitatively (9 confirmed boundary misses in round 1, concentrated at second-tier boundaries: walrus, ternary joins, container-element stores, match subjects sourced from fields, await results). First-tier boundaries (params, returns, var-decls from calls) are solid. The root design problem is that *form is decided independently at every consumer site* instead of being a fact carried by the expression -- exactly what `IR_DESIGN.md` Open Question 9 plans to fix. Until THIR lands, the cheapest structural mitigation is a single chokepoint helper (`convert(src_form, dst_form, type)`) that every emission site must route through, so a new boundary can't hand-roll a partial conversion. One genuine design hole: `tuple[...] | None` where the tuple has reference elements has **no coherent form at all** (`TupleType.is_value_type()` is unconditionally `True`, so Optional-of-tuple claims value repr while tuple elements independently choose pointer repr) -- this needs a design decision, not a patch (see theme: borrow form vs storage form).

**Auto-move is more aggressive than its alias analysis is strong -- that asymmetry is the single biggest source of critical UB.** The move optimizer fires on name-level last-use facts, but the alias map only tracks `x = y` name-to-name binds. Field aliases (`a = o.inner`), call-result borrows (`n = first(xs)`), generator objects borrowing their iterable, closures capturing by reference, and same-statement target/value splits are all invisible, and each blind spot is a silently emitted use-after-move/use-after-free (theme: ownership/move analysis). The right near-term posture is asymmetric conservatism: auto-move should require *provable absence of any borrow*, not absence of *known name aliases*; the full fix is MIR places/loans, which `docs/` already plans. `SAFETY_MODEL.md` promises "no use-after-move" as a pillar -- today that promise is only as strong as the weakest alias heuristic, and the doc's own "use-after-move rejection is TODO" admission should be treated as a release blocker for the safety story.

**Where Python parity is silently violated, the worst offenders are not exotic.** `dict.items()` loops copy values (mutation lost), `d.get(k, default)`/`setdefault` return copies (the canonical `d.setdefault(k, []).append(x)` idiom silently no-ops), match captures and walrus bindings of reference types copy, `bytearray` is classified a value type, list slicing *aliases* where CPython copies (`a[:]` -- the one divergence in the aliasing-permissive direction, making it doubly surprising), and positional-only parameters are silently dropped with misaligned defaults. For a language whose stated goal is "standard Python should work out of the box," the accepted-then-silently-different class matters more than any rejects-valid bug, because there is no diagnostic to teach the user the rule. Recommendation: every known parity divergence should either warn (the project already does this well at copy boundaries) or be a compile error until implemented; the audit found ~15 places where neither happens (section 3, themes: silent copies, parser fidelity).

**Sibling consistency is generally good but decays with construct age.** Newer constructs (async, match, readonly) consistently lag the older sibling: narrowing invalidation handles direct mutation but not closures/aliases; copy warnings fire for `append`/field-assign but not container literals; `with`-as targets miss the escaping/shadowing treatment assignment targets get; readonly wraps list-subscript results but not dict-subscript or tuple-element results; `prescan` marks `with`-as and tuple-unpack vars as reassigned but not scalar `for` vars. Each is filed individually; collectively they confirm CLAUDE.md's coupling thesis -- features added without a sibling sweep leave exactly these residues.

**The architecture is in better shape than the bug count suggests.** The nominal/structural TypeDef split is coherent and consistently applied; the two-phase sema with workspace-wide Phase-2 fixpoint is clean; per-compilation state is mostly on `Compiler` as mandated (three stragglers found beyond the documented exception); docs honestly track most known limitations. The codegen side mirrors sema in several places (`directly_implements_dynamic` duplicated verbatim, `compiler.py` re-deriving template-emission predicates, `frame_traits` disagreeing with `gen_async` about frame-slot ownership) -- these are exactly the THIR-migration frictions CLAUDE.md warns about, and they are accumulating.

## 3. Priorities (what to fix first)

P0 -- silently wrong code on mainstream constructs:
1. **Flow-fact loop/exception soundness** (theme: flow-sensitive analysis; 3 independent critical roots: loop-entry restore without body kill-sets, except-handlers analyzed from pre-try state, narrowing never invalidated by closure/alias mutation). These drive *check elision* (deref/bounds/div-zero), so every hole is emitted UB. A per-body assigned-name kill-set intersected at entry is a cheap sound fix; no fixpoint needed.
2. **match lowering cluster** (theme: match + gap findings; sema treats `case C():`/`case x:` on Optional subjects as catch-alls, codegen partitions them under `has_value()`, exhaustiveness then feeds `std::unreachable()`; `break` in a switch-lowered arm exits the switch instead of the loop; arm bindings dangle if the arm mutates the subject).
3. **Auto-move alias gating** (theme: ownership/move analysis).
4. **`finally` ordering and coverage** (gap findings G3/G35/G10: finally and `__exit__` run *before* the return expression is evaluated; suspended generators never run pending finallys on abandonment; nested defs inherit the enclosing finally context).
5. **Positional-only params silently dropped** (theme: parser fidelity; wrong values bind with zero diagnostics).
6. **dict access-path copies** (theme: silent copies; items()/get(default)/setdefault -- idiomatic, silent, mutation lost).
7. **View-lifetime bypasses** (gap findings G5/G6 + theme: dangling views; StrView/BytesView locals from view-returning expressions skip all lifetime tracking; `return a.strip()` returns a view of a dead local).

P1 -- soundness/parity with workarounds: second-tier borrow/storage boundaries, qname-blind identity (same-short-name types conflated across modules), readonly holes (dict subscript, tuples), `with`/`try` lowering gaps, Callable/Fn synthetic `is_readonly=True`.

P2 -- scale and hygiene: flow-fact save/merge quadratic behavior, mutation-propagation cycle fallback, protocol-conformance memoization, `_finalize_declarations` quadratic ModuleInfo rebuild, identifier-escaping chokepoint, slop inventory (section 8).

Known overlaps with `BUGS.md` (not new, but re-confirmed and in two cases re-rated): the escaping-`Callable` lambda capture-by-value divergence (BUGS.md "Silent capture-by-value divergence" entry) duplicates finding B-(lambda capture); loop-body container mutation UB (BUGS.md:313 family) duplicates B-(loop-body mutation); the non-null-Ptr loop back-edge entry (BUGS.md:342, rated LOW there) is argued up to high by the repro in B-(Ptr back-edge), since the same mechanism produces a genuine null deref with a possibly-null in-loop rebind.
## 4. Confirmed bugs (by theme)

### Theme: Flow-sensitive analysis: facts survive paths that kill them  (worst: critical, 10 findings)

#### B1. Narrowing facts survive calls to nonlocal-writing closures -> null-deref UB

- **Location**: tpyc/sema/narrowing.py:764-808, tpyc/sema/expressions.py:297-300
- **Severity / category**: critical / unsound-safety -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: Call-site fact invalidation only clears FIELD facts for NAMED ARGUMENTS (invalidate_field_facts_for_call iterates call.args; invalidate_field_facts_for_method_call adds the receiver). Name-level Optional narrowing is never invalidated by any call. A nested function declared with 'nonlocal x' that sets x = None is called between the 'if x is not None' guard and the use; the narrowing fact persists, codegen elides the null check, and the generated code dereferences nullptr. CPython raises AttributeError; TPy segfaults. The same gap applies to non_null_ptr_vars (Ptr facts) and to lambdas. Sibling note for gap-sweep: generators/async closures and Callable-escaping closures share the capture machinery and were not separately probed.
- **Evidence**: Repro /tmp/agents/n2_closure.py: def main(): x: Point | None = Point(1); def clear(): nonlocal x; x = None; if x is not None: clear(); print(x.x). Generated C++: 'auto clear = [&x]() { x = nullptr; }; if ((x != nullptr)) { clear(); std::cout << x->x ... }'. Built and ran: no diagnostics, EXIT=245 (SIGSEGV).
- **Fix direction**: When analyzing a call, kill narrowing/non-null facts for every variable written (nonlocal/global) by the callee closure -- sema already records nonlocal declarations; conservatively, any call to a local closure (or any opaque callable) should invalidate facts for all captured-and-written names. Same kill must apply to non_null_ptr_vars and value_ranges.

#### B2. Loop back-edge ignored: pre-loop narrowing facts survive body kills -> null-deref UB

- **Location**: tpyc/sema/init_tracker.py:110-137, tpyc/sema/statements.py:1100-1115
- **Severity / category**: critical / unsound-safety -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: apply_loop_entry_facts restores the state from just before the loop for the (single-pass) body analysis, so a fact established BEFORE the loop is assumed at the top of every iteration even when the body itself kills it (x = None). apply_loop_exit_facts intersects only the post-loop state; nothing applies the body's kill-set at loop entry. Iteration 2+ then executes a use that was proven only for iteration 1. NONE_SAFETY's loop policy ('body facts are iteration-local unless re-proven') covers facts created in the body but not pre-loop facts the body invalidates. The same single-pass design hole breaks readonly tracking (see separate finding) and presumably union/isinstance facts (stale std::get).
- **Evidence**: Repro /tmp/agents/n3_loop.py: x: Point | None = Point(1); if x is None: return; i = 0; while i < 3: print(x.x); if i == 1: x = None; i += 1. Generated: 'while ((i < 3)) { std::cout << x->x ... if ((i == 1)) { x = nullptr; } ... }' -- no diagnostics; ran: EXIT=245 (SIGSEGV) on iteration 3.
- **Fix direction**: Loop body entry facts must be the meet of (pre-loop facts, facts at the back-edge). Cheapest sound approximation pending the THIR fixpoint: pre-scan the loop body for assigned names (prescan already collects them for hoisting) and drop narrowing/non-null/range facts for those names before analyzing the body. Apply the same kill to for-loops and to readonly scope-type merging.
- *Independently found as*: "Loop-entry fact restore resurrects narrowing the loop body kills -> UB on empty optional" (`sema-core`)

#### B3. Except handlers analyzed from pre-try state; facts killed inside try are resurrected -> null-deref UB

- **Location**: tpyc/sema/statements.py:1874-1925 (_analyze_try_throw, handler restore at 'self.init.restore(before)')
- **Severity / category**: critical / unsound-safety -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: _analyze_try_throw analyzes each except handler 'as a separate branch from pre-try state' (init.restore(before)). But an exception can be thrown at ANY point in the try body, including after the body reassigned a narrowed variable to None. The handler therefore sees a narrowing fact (x non-None) that no longer holds on the actual throw path, the null check is elided, and the handler dereferences nullptr. Correct handler-entry state is the meet over all potential throw points (conservatively: pre-try facts minus facts killed anywhere in the try body). The return-tier handler (_analyze_try_return) restores the same way and shares the hole; finally bodies were not separately probed (flag for gap-sweep, with async/with-statement variants).
- **Evidence**: Repro /tmp/agents/n6_except.py: x narrowed non-None before try; try body does 'x = None; risky(1)' where risky raises; except ValueError: print(x.x). Generated: 'try { x = nullptr; risky(1); } catch (const ::tpy::ValueError&) { std::cout << x->x ... }' -- no diagnostics; ran: EXIT=245 (SIGSEGV).
- **Fix direction**: Before analyzing handlers, intersect 'before' facts with the kill-set of the try body (drop narrowing/non-null/range facts for any name the try body assigns), mirroring the loop fix. Check _analyze_try_return and finally-only tier for the same pattern.

#### B4. value_range bounds-check & div-zero elision survives loop back-edge -> silent OOB read / div-by-zero UB

- **Location**: tpyc/sema/init_tracker.py:135 (apply_loop_entry_facts restores value_ranges from `before`), tpyc/sema/statements.py:1104-1112 (while) and 1180-1183 (for), consumers tpyc/sema/expressions.py:1328-1334 (bounds_safe) and 1347-1349 (divisor_non_zero)
- **Severity / category**: critical / ub -- reproduced: yes -- found by `sema-stmt-flow`
- **Problem**: Integer range facts that drive bounds-check elision (is_bounded_by_len/is_non_negative) and div-by-zero elision (non_zero) are flow facts proven on the path REACHING the loop. At loop entry apply_loop_entry_facts restores value_ranges from the pre-loop snapshot, and the loop body is analyzed in a single forward pass. A statement that mutates the tracked variable (e.g. `i += 5`, `d = d - 1`) clears the fact via update_after_write/aug-assign pop, but only for statements appearing AFTER it in source order. A subscript/division ABOVE the mutation keeps its elision flag, which is correct on iteration 1 but unsound on iterations 2+ where the variable has been mutated out of the proven range. No fixpoint / back-edge invalidation kills the fact for variables assigned anywhere in the loop body. Result: the compiler silently emits raw `vec[i]` (no tpy::__getitem__ bounds check) and `div_floor` (no zero check) for plausible manual-index/manual-divisor while-loops. The non_null_ptr_vars sibling has the identical back-edge defect (already noted at BUGS.md:342 but rated LOW; my repro below shows it is real null-deref UB, not identity). narrowed_types is exposed the same way but Optional storage uses std::optional so it is comparatively safe; @dynamic pointer-variant narrowing is not.
- **Evidence**: Bounds repro (/tmp/agents/r1c.py):
  def go(xs: list[int], start: int, n: int):
    i = start
    if i < len(xs):
      if i >= 0:
        k = 0
        while k < n:
          print(xs[i]); i = i + 5; k = k + 1
  go([10,20,30], 0, 3)
Generated body: `std::cout << xs[static_cast<std::size_t>(i.to_fixed_check<int32_t>())]` (RAW index, no check). Control (no loop/mutation) emits `::tpy::__getitem__(xs, ...)` (checked). Running prints: 10, 0, 0  -- xs[5] and xs[10] read out of bounds on a 3-element vector (CPython raises IndexError).
Div repro (/tmp/agents/r3b.py, d!=0 before loop, `d = d - 1` in body): loop emits `::tpy::div_floor<int32_t>(100, d)` (unchecked) vs control `::tpy::div_check<int32_t>(100, d)`; d reaches 0 on the 3rd iteration -> integer divide-by-zero UB.
Ptr repro (/tmp/agents/r4_ptr.py, `p is not None` before loop, `p = q` with q null in body): loop emits raw `p->v`, cont...
- **Fix direction**: At loop entry, before analyzing the body, drop value_ranges and non_null_ptr_vars (and narrowed_types for unsafe pointer-variant narrowings) for every name that is assigned/aug-assigned/mutated anywhere in the loop body. The prescan already gathers reassigned/aug_assigned vars; feed that set into apply_loop_entry_facts to kill the corresponding elision facts (a proper back-edge widening). This is the MIR/THIR place-loan invalidation the design anticipates; do it uniformly for while, for-each, async-for, and the orelse re-entry.

#### B5. Field-path narrowing facts not invalidated through aliases or global roots -> UB read of disengaged optional

- **Location**: tpyc/sema/narrowing.py:764-808
- **Severity / category**: high / unsound-safety -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: Field-fact invalidation is purely name-rooted: a method call invalidates facts rooted at the receiver NAME and named args only. Two unsound consequences: (a) alias case -- h2 = h; if h.p is not None: h2.clear(); use h.p -- the call invalidates h2.* but not h.*, though both name the same object (plain local aliasing, explicitly allowed by SAFETY_MODEL.md); (b) global-root case -- a no-arg call clear() that writes global h's field leaves the h.p fact alive. In both, codegen elides the null check and emits (*h.p).x on a disengaged std::optional -- UB that silently reads stale storage (observed printing the old value where CPython raises AttributeError). READONLY_DESIGN admits narrowing preservation is best-effort for the GLOBAL scenario, but the local-alias case is not documented anywhere and is mainstream reference-type code.
- **Evidence**: Repro /tmp/agents/n4_alias_field.py: h2 = h; if h.p is not None: h2.clear(); print(h.p.x) -- generated 'if ((h.p.has_value())) { h2.clear(); std::cout << (*h.p).x ... }', runs printing 1 (stale value via UB read; CPython: AttributeError). /tmp/agents/n5_global_field.py (global h, no-arg clear()) generates the same unchecked '(*h->p).x' and prints stale 1.
- **Fix direction**: Field-path facts on reference types are only sound if no other alias can reach the root. Until escape analysis exists, conservatively clear ALL field-path facts (or at least all facts whose root is a reference type that has been aliased / is a global) on any non-readonly call, matching the documented 'invalidate more aggressively' design decision. The deref-view keys (prescan.py INVARIANT comment) need the same sweep.

#### B6. Non-null Ptr provenance survives loop back-edge: real null-deref UB (tracked but under-rated)

- **Location**: tpyc/sema/flow_facts.py:148 + tpyc/sema/init_tracker.py:127,154 (non_null_ptr_vars restore/intersect at loop boundaries), tpyc/sema/statements.py:1111 (while ptr_nn application); existing entry BUGS.md:342
- **Severity / category**: high / ub -- reproduced: yes -- found by `sema-stmt-flow`
- **Problem**: BUGS.md:342 documents that non-null Ptr provenance is not widened at loop back-edges and rates it LOW because the cited test reassigns the var to itself (identity). The same mechanism produces a genuine null-pointer dereference when the in-loop reassignment binds a possibly-null pointer: `if p is not None:` before the loop marks p non-null, the body emits raw `p->field`, and `p = q` (q null) inside the loop is not re-checked on iteration 2+. This is the same architectural root cause as the value_range finding above (single-pass body analysis, no back-edge fact kill) and should be fixed together. Flagging because the LOW rating undersells it: it is silently-emitted memory-unsafety on a plausible linked-list/iteration pattern.
- **Evidence**: /tmp/agents/r4_ptr.py: `if p is not None:` guards a while loop whose body does `print(p.v); p = q` where q is a null Ptr[Node]. Loop body codegen: `std::cout << p->v` (raw, no deref_check). Control function emits `::tpy::deref_check(p).v`. On iteration 2 p==nullptr -> UB.
- **Fix direction**: Fold into the loop back-edge invalidation fix: at loop entry, drop non_null_ptr_vars for any pointer variable reassigned in the body (same prescan-driven kill set as value_ranges). Re-rate BUGS.md:342 up from LOW given a clean UB repro exists.

#### B7. CFGBuilder narrowing-kill misses reassignments inside leaf compounds: resume cases re-establish stale narrowings

- **Location**: tpyc/codegen_cpp/resumable_cfg.py:722-746 (_kill_narrowings), 760-776 (_build_block applies kill only to top-level stmts), 840 (leaf compound appended without recursion)
- **Severity / category**: medium / unsound-safety -- reproduced: yes -- found by `codegen-orchestration`
- **Problem**: _kill_narrowings is applied per top-level statement in _build_block; a leaf compound (e.g. an inner `if` with no suspension) is appended whole to the BB, and any reassignment of a narrowed variable inside it is never seen. Subsequent BBs (including resume cases after a later suspension) keep the stale entry_narrowings and re-emit the narrowed extraction (std::get<Dog>) for a variable that may now hold a different alternative -- which, once the frame_slot-deref bug above is fixed, becomes a runtime std::bad_variant_access where CPython runs fine. Sema's own flow facts correctly un-narrow (field accesses after the inner if are rejected), so today the harm is masked for the local case by the build error above and by sema for uses; but the extraction is emitted unconditionally at the resume case even when nothing downstream uses the narrowed binding.
- **Evidence**: Dump of /tmp/agents/async_narrow_kill.py (`if isinstance(y, Dog): if flip: y = Cat(); await asyncio.sleep(0); print(...)`) shows case S_INITIAL emitting `auto& __y = std::get<Dog>(y); if (flip) { y.emplace(Cat()); } ...` and case S_RESUME_0 re-emitting `auto& __y = std::get<Dog>(y);` after y may hold Cat -- a wrong-alternative extraction at the resume point.
- **Fix direction**: Recurse _kill_narrowings into the sub_bodies of leaf compounds appended to a BB (conservatively kill any name assigned anywhere inside), or better, source entry_narrowings from sema's flow facts at the statement boundary instead of the builder's own shadow tracking (the builder re-derives a fact sema already computes -- exactly the consumer-side re-derivation CLAUDE.md's THIR guidance warns about). Gap-sweep: also leaf for/while loop variables and with-targets that shadow a narrowed name.

#### B8. Readonly alias rebind across loop back-edge accepted by sema (same single-pass hole), C++ backstop only

- **Location**: tpyc/sema/init_tracker.py:110-137, tpyc/sema/statements.py:1100-1115 (while body single-pass)
- **Severity / category**: medium / unsound-safety -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: The readonly variant of the loop back-edge hole: a local that is mutable at loop entry but rebound to a readonly source inside the body is mutated at the top of the body (iteration 2 writes through the readonly param). The documented conservative union merge ('readonly on either branch -> readonly after join') is applied only at the loop EXIT, never fed back to body entry. Sema accepts with zero diagnostics; today the C++ build fails on 'const Point* -> Point*' conversion, so no silent mutation -- but the rejection is a raw g++ error, and the backstop is incidental (a borrow shape with shallow const, like the tuple finding, would mutate silently).
- **Evidence**: Repro /tmp/agents/r18_loop_readonly.py: def f(p: readonly[Point]): alias = Point(0); i = 0; while i < 2: alias.x = 99; alias = p; i += 1. No TPy diagnostics; emitted 'alias->x = 99; alias = &(p);'; C++ build error: invalid conversion from 'const Point*' to 'Point*'.
- **Fix direction**: Same fix as the narrowing back-edge finding: apply the loop body's readonly-merge (scope-type union) at body ENTRY (pre-scan assigned names, pre-merge readonly status) before analyzing the body, not only at exit.

#### B9. Union REASSIGNMENT does not narrow (Optional reassignment and union initial-declaration both do) -- rejects valid code

- **Location**: tpyc/sema/narrowing.py:710-723 (update_after_write re-narrows only OptionalType targets), tpyc/sema/statements.py:3529-3536 (union narrowing only when existing_type is None)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: update_after_write re-establishes narrowing after assignment only for Optional-typed targets ('if not isinstance(inner_target, OptionalType): return'). Union locals get assignment narrowing only on the INITIAL declaration (statements.py existing_type is None guard). So 'u: A | B = A(1); u = B(7); print(u.y)' is rejected with 'Cannot access field y on type A | B' although CPython (and the sibling Optional pattern 'o: P | None = None; o = P(1); o.x', which compiles fine) accept it. Same rejection inside an isinstance branch after reassignment. This is exactly the Optional-vs-Union sibling asymmetry class. Workaround: re-narrow via isinstance.
- **Evidence**: Repro /tmp/agents/n8_union_reassign_plain.py: u: A | B = A(1); u = B(7); print(u.y) -> 'error: Cannot access field y on type A | B'. Optional sibling /tmp/agents/n10_opt_assign.py (o: Point | None = None; o = Point(1); print(o.x)) compiles clean.
- **Fix direction**: In update_after_write, when the (unwrapped) target type is a UnionType and rhs_type matches a single member, set narrowed_types[name] to that member (and emit the then_type_facts codegen fact like the declaration path does, so variant access stays consistent).

#### B10. is/is-not None rejected when declared Optional is readonly-wrapped (assignment-narrowed alias of readonly source)

- **Location**: tpyc/sema/expressions.py:799-807 (declared-type preservation for identity checks), tpyc/typesys.py:2336-2339 (is_union_or_optional_type does not unwrap qualifiers)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: For is/is-not None on a flow-narrowed name, the analyzer restores the DECLARED Optional type (expressions.py:799-807) so the check passes even though narrowing already resolved the inner type. But the gate is_union_or_optional_type(declared) does not unwrap ReadonlyType (or OwnType), so when the declared type is readonly-wrapped -- e.g. 'q: Point | None = p' with p: readonly[Point], where the decl inherits ReadonlyType from the init -- the restore is skipped, the narrowed 'readonly[Point]' reaches the nullable check, and compilation fails with "'is' / 'is not' can only compare Optional/Ptr/union types with None, got readonly[Point] and None". The identical non-readonly pattern compiles. OwnType-wrapped declared Optionals likely share the gap (untested -- flag for gap-sweep).
- **Evidence**: Repro /tmp/agents/r1_optional_wrap.py: def f(p: readonly[Point]): q: Point | None = p; if q is not None: ... -> 'error: ... got readonly[Point] and None'. Plain sibling /tmp/agents/r13_narrowed_isnone.py (q: Point | None = Point(1); if q is not None: print(q.x)) compiles clean.
- **Fix direction**: Unwrap qualifiers (Readonly/Own/Ref) before the is_union_or_optional_type test at expressions.py:799-807 and re-wrap on restore so readonly enforcement is preserved on the narrowed branch.

### Theme: Analyzer state management  (worst: critical, 4 findings)

#### B11. Nested-def deepcopy snapshot orphans id-keyed AST facts -> retro int widening lost, truncated output

- **Location**: tpyc/sema/context.py:1105-1111, tpyc/sema/scope_tracker.py:76-95, tpyc/sema/local_deduction.py:282
- **Severity / category**: critical / miscompile -- reproduced: yes -- found by `sema-core`
- **Problem**: ScopeTracker.nested_def_scope isolates nested-def analysis via ctx.save_function_state() = deepcopy(self.func), and restore_function_state installs the DEEPCOPY as the live state. After any nested def, the enclosing function's var_decl_by_name (and pending_loop_vars, write_history, current_function, current_ns chain) hold cloned AST nodes whose id() differs from the real AST. Retroactive type promotion writes ctx.var_types[id(var_decl)] (local_deduction.py:282) against the clone, so codegen (keyed on the real node) never sees the promoted type. The compiler even emits a warning claiming the promotion happened. Secondary cost: deepcopy clones the entire Namespace parent chain (global_ns -> macro_ns -> builtins_ns, every binding and type) on every nested def -- a per-nested-def performance cliff.
- **Evidence**: Repro: def main(): x = 5; def inner(): print("inner"); inner(); x = 10000000000; print(x). Generated C++: `int32_t x = 5; ... x = 10000000000;` -- runtime prints 1410065408 (truncated) while emitting warning 'Integer literal 10000000000 is outside default Int32 range; promoting variable to int (BigInt).' Control without the nested def correctly emits `::tpy::BigInt x = ::tpy::BigInt(5);` and prints 10000000000.
- **Fix direction**: Stop deepcopying the live function state: either swap in a fresh FunctionTrackingState for the nested def and restore the ORIGINAL object (no copy needed -- the original is untouched while the nested def runs), or exclude AST-node-holding maps (var_decl_by_name, pending_loop_vars, write_history, current_function, current_ns) from the copy and restore them by reference. Any id()-keyed side table (var_types, declared_var_types) is corrupted by node cloning; the THIR migration note about id-keyed side tables applies directly. Gap-sweep: trial_scope also uses save_function_state -- check whether facts written during a *committed* overload-trial suffer the same clone aliasing.

#### B12. @nocopy propagation is definition-order dependent; forward field refs silently lose non-copyability

- **Location**: tpyc/sema/analyzer.py:1147-1169, tpyc/sema/context.py:930-974
- **Severity / category**: medium / unsound-safety -- reproduced: yes -- found by `sema-core`
- **Problem**: _propagate_nocopy walks records in definition order and relies on ctx.is_type_nocopy, which only consults the already-set RecordInfo.is_nocopy flag (it does not walk a record's fields). When class A (defined first) holds a field of type B (defined later) and B becomes nocopy via its own field, A is processed before B's flag is set, so A is never marked nocopy. The same program with definitions reordered gets a clean sema error. Consequence: codegen omits the deleted copy ctor on A, copy(A) passes sema, and the user gets a raw C++ 'use of deleted function B(const B&)' error from inside std::vector instead of the sema diagnostic; other is_nocopy consumers (copy-warning-vs-error decisions, yield-copy checks) see a wrong answer for A. TPy deliberately supports forward type references (its resolver is module-wide), so this is reachable.
- **Evidence**: Order (A: bs: list[B]; B: n: N; @nocopy N): zero sema diagnostics; emitted `struct A` has no deleted copy ctor; building fails inside std::vector copy with `B(const B&) = delete`. Reversed order (N, B, A): sema error 'Cannot copy non-copyable type A (field bs has non-copyable type list[B])' and `struct A { A(const A&) = delete; // non-copyable (field 'b') }`.
- **Fix direction**: Make propagation order-independent: iterate to fixpoint over the module's records (or compute SCC/topological order over the field-type graph), or make is_type_nocopy walk fields recursively with a cycle guard (it already does so in is_type_non_copyable, which is why the field-store path catches the same shape). Gap-sweep: check whether Send/Sync auto-derivation in registration.py:2076-2086 has the same same-module forward-ref order dependence (it reads field.type.is_send() at registration time).

#### B13. Class constant sharing a module-level Final's name erases the Final from analyzed_finals

- **Location**: tpyc/sema/analyzer.py:2353-2374
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-core`
- **Problem**: _analyze_class_constants appends every class-constant name to added_finals unconditionally after analyzed_finals.add(cc_name), then rolls back with analyzed_finals.difference_update(added_finals). If a class constant shares its name with a module-level Final that was already in analyzed_finals, the rollback removes the module-level entry. Every later compile-time-constant validation in the module (other classes' constants referencing the module Final) then falsely rejects.
- **Evidence**: Repro: `A: Final[Int32] = 1` at module level; `class First: A: Final[Int32] = 2`; `class Second: B: Final[Int32] = A + 1` -> error: "class constant 'Second.B' requires a compile-time constant initializer; 'A' is not a Final constant". Removing class First makes it compile cleanly (verified).
- **Fix direction**: Only record cc_name in added_finals when it was not already present: `if cc_name not in self.ctx.analyzed_finals: added_finals.append(cc_name)` before the add. Same shadow-rollback pattern should be audited anywhere a temporary name set is rolled back with difference_update.

#### B14. Duplicate plain `def` produces no diagnostic, emits C++ redefinition; Phase-1 facts stamped on wrong FI

- **Location**: tpyc/sema/analyzer.py:1362-1366, tpyc/sema/registration.py:3080
- **Severity / category**: low / rejects-valid -- reproduced: yes -- found by `sema-core`
- **Problem**: Two same-name, same-signature plain `def f` (no @overload) pass sema silently and both bodies are emitted, producing a C++ 'redefinition of f' build error (CPython: second def wins). Additionally _analyze_function stamps Phase-1 mutation facts on registry.get_function(name)[-1]; with two registered entries the FIRST body's facts land on the SECOND def's FunctionInfo, and the second body's facts are dropped by the `direct_mutated_params is None` gate -- so even if registration semantics were fixed to keep both, mutation propagation would run on cross-wired facts. With differing signatures the second def correctly shadows the first at resolution (f(5) errors against the str signature), so behavior between the two duplicate shapes is inconsistent.
- **Evidence**: dupdef.py: `def f() -> Int32: return 1` then `def f() -> Int32: return 2`; --dump-code shows two `int32_t f()` definitions, no sema diagnostic; build fails: "error: redefinition of int32_t f()... previously defined here".
- **Fix direction**: Detect a non-@overload redefinition at register_function (name already bound by a non-stub def in the same module) and either error ('duplicate definition of f; use @overload') or implement CPython second-wins by replacing the FI and skipping codegen for the first body. The overloads[-1] stamping convention then stays single-target.

### Theme: match statement: exhaustiveness and arm-dispatch codegen  (worst: critical, 11 findings)

#### B15. Optional match: wildcard/capture arm never matches None; exhaustive tail emits std::unreachable -> UB

- **Location**: tpyc/codegen_cpp/match.py:1801-1815, tpyc/codegen_cpp/match.py:1828-1831, tpyc/codegen_cpp/match.py:238-253
- **Severity / category**: critical / ub -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: _partition_optional_cases classifies wildcard/capture arms as inner_cases (value-only). _gen_match_optimized_optional then wraps the entire inner dispatch in `if (__match_subject.has_value())`, so a None subject skips the wildcard arm entirely. In CPython `case _:` (and `case x:`) matches None. Worse: the wildcard makes sema mark the match exhaustive, so when all arms terminate _emit_match_unreachable_tail emits ::std::unreachable() at the fall-through point -- a None subject runs straight into UB. With non-terminating arms it is a silent wrong-output miscompile (arm skipped).
- **Evidence**: Repro m4: `def f(v: Int32 | None): match v: case 5: print("five"); case _: print("other")` -- f(None) prints NOTHING under TPy (CPython: "other"). Generated: `if (__match_subject.has_value()) { ...switch... default: { other } }` with no else. Repro m6 (arms return): generated `int32_t f(std::optional<int32_t> v) { if (v.has_value()) { switch... return...; } ::std::unreachable(); }` -- f(None) executes std::unreachable().
- **Fix direction**: In _partition_optional_cases, treat wildcard/capture arms as matching None too: either bail out of the optimization when a wildcard/capture arm exists without a preceding None arm, or route the None side into the first wildcard/capture arm. Also audit _gen_match_if_elif_optional's wildcard 'needs_deref' arm (it guards capture on has_value, dropping None there as well -- CPython binds x=None). Sibling check for the gap sweep: union-with-None subjects via the switch paths handle monostate explicitly and look fine.

#### B16. Optional match: non-None arm treated as catch-all; None path lowered to std::unreachable() (silent UB)

- **Location**: tpyc/sema/match.py:263-283, tpyc/sema/match.py:951-1004
- **Severity / category**: critical / ub -- reproduced: yes -- found by `sema-methods-protocols`
- **Problem**: In analyze_match, a class pattern on an Optional subject with no constraining sub-patterns sets had_wildcard=True (line 269-274: '(is_record or is_optional) and isinstance(pat, TpyClassPattern)'), and a bare capture pattern does too (line 263). But on an Optional subject, 'case Point():' and 'case q:' do NOT match None in CPython ('case q' matches None but binds None, while TPy binds the inner type and derefs). Consequences: (a) the match is marked exhaustive with no warning, and codegen emits a null guard followed by ::std::unreachable() -- calling with None executes UB and returns garbage (observed: printed 0 where CPython prints None); (b) a legitimate 'case None:' arm AFTER 'case Point():' is rejected with 'unreachable case after wildcard pattern' (rejects-valid, match.py:161-164). Three reproduced variants. The record-subject branch of the same condition is fine (a concrete record always matches); only the is_optional half is wrong.
- **Evidence**: Repro 1 (UB, ran end-to-end):
  def f(p: Point | None) -> Int32:
      match p:
          case Point():
              return p.x
  print(f(Point(3))); print(f(None))
Generated C++:
  int32_t f(const Point* p) {
      if (__match_subject != nullptr) { ... return p->x; }
      ::std::unreachable();
  }
Observed run output: '3' then '0' (UB; CPython prints 3 then None). No compiler warning emitted.
Repro 2 (bare capture 'case q: return q.x' with f(None)): identical unreachable() shape, q bound to *nullptr deref path.
Repro 3 (rejects-valid): 'case Point(): ... case None: ...' -> 'opt_match1.py:13: error: unreachable case after wildcard pattern'.
- **Fix direction**: In the had_wildcard condition, drop is_optional from the class-pattern arm (a class pattern on Optional covers only the non-None side) and make a bare capture on Optional bind Optional[T] (CPython binds None too) or require coverage of None separately. Exhaustiveness for Optional should require BOTH a None arm and a non-None catch-all. Codegen must not emit std::unreachable() unless sema proved both sides covered. Sibling check for the gap-sweep: union subjects with a None member and the polymorphic-dispatch path may have analogous catch-all assumptions.
- *Independently found as*: "match class-pattern over an Optional subject is treated as a wildcard: bogus 'unreachable case' rejection AND missing-return UB on the None path" (`hunt-borrow-storage`)

#### B17. match on int/scalar (non-enumerable) subject wrongly marked exhaustive -> std::unreachable() miscompile with no warning

- **Location**: tpyc/sema/match.py:556-600 (_match_missing_cases returns [] fall-through for BigInt/int), tpyc/sema/match.py:294 (is_exhaustive = not missing), tpyc/codegen_cpp/match.py:250-253 (_emit_match_unreachable_tail), shared assumption in tpyc/liveness.py:160-161 (stmts_terminate for TpyMatch)
- **Severity / category**: critical / miscompile -- reproduced: yes -- found by `sema-stmt-flow`
- **Problem**: _match_missing_cases enumerates uncovered cases only for UnionType, enum, Optional, bool, LiteralType and user records; for an `int`/BigInt (or any other non-enumerable scalar) subject it falls through to `return []`. An empty missing list sets stmt.is_exhaustive = True AND suppresses the non-exhaustive warning. For a match on an int with a few literal `case` arms and a trailing fall-through `return`/statement (the idiomatic 'switch with default after'), codegen's _emit_match_unreachable_tail then emits `::std::unreachable();` for the no-match path because is_exhaustive is True and all listed arms terminate -- making the user's intended default path dead code. Any subject value not covered by a literal arm hits std::unreachable() (UB). liveness.stmts_terminate (this reviewer's file) encodes the same false assumption: it returns True for a match whose listed arms all terminate regardless of exhaustiveness, so a non-exhaustive match as the last statement of a branch can also mis-drive last-use/move (use-after-move) liveness.
- **Evidence**: /tmp/agents/r5_match.py:
  def pick(tag: int) -> str:
    s = "owned-" + str(tag)
    match tag:
      case 1: return s + "-one"
      case 2: return s + "-two"
    return s + "-default"
  print(pick(1)); print(pick(9))
Generated pick(): if/else-if on tag==1 / tag==2 followed by `::std::unreachable();` then a dead `return str_concat(s, "-default")`. Compiles with NO warning (verified: tpyc emits nothing). Running the binary prints:
  owned-1-one
  owned-1-one
(pick(9) should print 'owned-9-default'; instead hits std::unreachable() UB and returns a stale value). The enum analog (/tmp/agents/r6_enum.py) correctly warns and stays non-exhaustive, proving the gap is specific to scalar subjects with no missing-case enumerator.
- **Fix direction**: In _match_missing_cases, for a non-enumerable subject (int/BigInt/float/str without LiteralType, etc.) where exhaustiveness cannot be proven, return a sentinel non-empty 'missing' (like the [None] used for user records) so is_exhaustive is False and the non-exhaustive warning fires. Then codegen will not emit std::unreachable() and liveness/stmts_terminate must likewise treat such a match as falling through. Also harden liveness.stmts_terminate / _analyze_match to consult stmt.is_exhaustive rather than assuming all-arms-terminate implies the match terminates.

#### B18. Guarded capture arm after earlier arms: both arms execute (first-match violation)

- **Location**: tpyc/codegen_cpp/match.py:1361-1374, tpyc/codegen_cpp/match.py:1412-1422
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: _gen_match_if_elif (str/float/bool subjects) lowers a capture-with-guard arm at i>0 by CLOSING the if/else chain (`}`), emitting the binding at top level, then a standalone `if (guard)`. The else-linkage to earlier arms is severed, so when an EARLIER arm matched and ran, control still falls into the capture binding and guard test -- a second arm body executes. Same chain-break exists in the TpyAsPattern wildcard/capture-with-guard branch (1412-1422).
- **Evidence**: Repro m1: `match s: case "a": print("one"); case x if len(x) >= 1: print("two")` with s="a". Generated: `if (subj == "a") { one } auto& x = subj; if (len(x) >= 1) { two }`. Observed runtime output: "one\ntwo" (CPython: "one").
- **Fix direction**: Keep the chain intact: emit the capture arm as `} else { auto& x = ...; if (guard) { body } }` (binding inside the else block), mirroring how _emit_switch_groups emits bindings before the guard chain inside the case block. Note the same statement-walk shape exists in the trailing-arms section of _gen_match_switch_str (there it is protected by goto end_label, so only the if_elif paths are broken).

#### B19. As-pattern with guard: guard failure swallows all later arms

- **Location**: tpyc/codegen_cpp/match.py:1395-1405
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: _gen_match_if_elif lowers `case LIT as v if guard:` as `if (cond) { bind v; if (guard) { body } }` and then chains the NEXT arm as `} else if (...)` onto the OUTER cond-if. When cond matches but the guard fails, Python falls through to later arms (e.g. a duplicate-literal unguarded arm, which sema explicitly permits for guarded arms); the generated C++ skips them because the outer if already 'took' the match. Silent wrong output, no diagnostic.
- **Evidence**: Repro m5: `match s: case "a" as v if len(v) > 5: ...; case "a": print("plain"); case _: print("other")` with s="a". Generated: `if (subj=="a") { auto& v=subj; if (len(v)>5) {...} } else if (subj=="a") { plain } else { other }`. TPy prints nothing; CPython prints "plain".
- **Fix direction**: Guard failure must fall through: use the standalone-if + goto end_label shape (as _gen_match_guarded_record does) or fold the binding-dependent guard via a statement-expression into a single chained condition. This is sema/codegen drift: sema's seen_values restore for guarded arms (sema/match.py:174-203) deliberately allows the later duplicate arm that codegen then makes unreachable.

#### B20. Guard referencing the capture binding emitted before the binding exists (Optional if/elif path)

- **Location**: tpyc/codegen_cpp/match.py:2159-2182
- **Severity / category**: high / rejects-valid -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: _gen_match_if_elif_optional lowers a guarded capture arm as `if (has_value && <guard>) { auto& x = (*__match_subject); ... }` -- the guard expression references the capture name x BEFORE its C++ declaration. Normally a build failure on valid Python; if an outer variable with the same name is in scope, it would compile and silently read the wrong variable (miscompile). The sibling switch path (_emit_switch_groups:903-908) explicitly binds before the guard chain; this path drifted.
- **Evidence**: Repro m3: `match v: case x if x > 5: print("big"); case None: ...; case _: ...` (None arm not a prefix forces the if/elif path). Generated: `if (__match_subject.has_value() && (x > 5)) { auto& x = (*__match_subject); ... }` -- x used before declaration.
- **Fix direction**: Split into `if (has_value) { auto& x = *subj; if (guard) { body } else goto next; }` or hoist the binding above the condition like _gen_match_if_elif's i==0 capture path / _emit_switch_groups do. Must preserve fallthrough to later arms on guard failure (see the as-pattern fallthrough finding).
- **Verifier adjustment**: Real and worse than described. Base repro (Int32|None subject, 'case x if x > 5' first, 'case None' second) emits: 'if (__match_subject.has_value() && (x > 5)) { auto& x = (*__match_subject); ...' -- guard reads x before its C++ declaration, so plain valid Python fails at the C++ build (rejects-valid). But the shadowing variant is a verified SILENT MISCOMPILE, not hypothetical: with an outer 'x = 100' in scope and f(1), TPy emits 'int32_t x = 100; ... if (has_value() && (x > 5)) { x = (*__mat...

#### B21. Optional match exhaustiveness only ever reports missing 'None' -- only-None-arm match marked exhaustive, value path is std::unreachable()

- **Location**: tpyc/sema/match.py:576-579
- **Severity / category**: high / ub -- reproduced: yes -- found by `sema-methods-protocols`
- **Problem**: _match_missing_cases for OptionalType returns ['None'] if None was not seen, else [] -- it never reports the missing non-None side. A match with only 'case None:' is therefore marked exhaustive (stmt.is_exhaustive=True, no warning), and the generated function falls into ::std::unreachable() whenever the subject is non-None. Distinct root cause from the had_wildcard bug: this is the missing-cases enumerator itself.
- **Evidence**: Repro (dump-code, no warning emitted, exit 0):
  def f(p: Point | None) -> Int32:
      match p:
          case None:
              return -1
Generated:
  int32_t f(const Point* p) {
      if (__match_subject == nullptr) { return -1; }
      ::std::unreachable();
  }
f(Point(3)) executes unreachable() -> UB. CPython returns None.
- **Fix direction**: OptionalType arm of _match_missing_cases must also check that the non-None side is covered (a class/capture arm or wildcard); return e.g. [str(subject_type.inner)] when only None was matched.

#### B22. case None on union subject crashes codegen when any arm is guarded

- **Location**: tpyc/codegen_cpp/match.py:1078-1110, tpyc/codegen_cpp/match.py:734-741
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: The unguarded union switch path handles `case None:` (TpyLiteralPattern value None -> monostate variant index, match.py:734). The guarded path _gen_match_guarded_union -- entered whenever ANY arm has a guard or field-value sub-pattern -- has no TpyLiteralPattern branch in its arm-collection loop and raises CodeGenError. Sema fully accepts the program (sema/match.py:634-646 validates case None against union-with-None), so this is sema/codegen drift: adding a guard to one arm makes a previously-compiling match fail.
- **Evidence**: Repro m2: `def f(v: A | B | None, flag: bool): match v: case A() if flag: ...; case None: ...; case _: ...` -> `m2.py: error: Unsupported pattern in guarded union match: TpyLiteralPattern`. Without the guard the same match compiles.
- **Fix direction**: Handle TpyLiteralPattern(None) in the guarded collection loop: map it to the NoneType variant index (reuse _variant_index(subject_type, NoneType())) and treat it as an unguarded class-like arm for that index.

#### B23. match on a storage-form Optional source (field subject) emits pointer-form tests: `std::optional<T> == nullptr` -- ill-formed C++

- **Location**: tpyc/codegen_cpp/match.py:1824-1828, tpyc/codegen_cpp/match.py:2088-2091
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: The Optional-subject match codegen decides uses_ptr from the TYPE alone (subject_type.uses_pointer_repr()), not from the source's actual form. A field-sourced subject (`match h.opt:`) binds `auto& __match_subject = h.opt;` -- storage form std::optional<Box> -- but uses_pointer_repr() is True for Optional-of-record, so the null tests emit `__match_subject == nullptr` / `!= nullptr`, which is ill-formed for std::optional. Param-sourced subjects (already T*) work. Sibling of BUGS.md:180 (Optional-of-union match) but for plain Optional[T], the far more common shape; untracked. The form-from-type-not-source confusion is exactly the duality bug class; the if-narrowing path solved it with is_storage_form_optional_source + optional_to_ptr, the match path didn't.
- **Evidence**: Repro /tmp/agents/bf/h2_match_opt.py: `match h.opt: case None: pass; case Box() as bb: bb.val = 99` where opt is a `Box | None` field. Generated: `auto& __match_subject = h.opt; if (__match_subject == nullptr) {` -> g++ 'no match for operator== (operand types are std::optional<Box> and std::nullptr_t)'. No TPy diagnostic.
- **Fix direction**: Lift the subject at binding when the source is storage-form (`Box* __match_subject = ::tpy::optional_to_ptr(h.opt);`), mirroring the var-decl auto-lift; or derive null_cond from the bound expression's actual form. Same fix must cover the second copy of the logic at match.py:2088-2091 and the resumable-match path.

#### B24. match capture of a reference-type subject copies the object (case P() as q / bare case q)

- **Location**: tpyc/codegen_cpp/match.py:953-1001 (_emit_binding pre-declared assignment path)
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `hunt-silent-copy`
- **Problem**: A match capture binding of a class-instance subject is emitted as assignment into a hoisted `std::optional<P>` local: `std::optional<P> q; q = __match_subject;` -- a full copy. Mutations through `q` are invisible on the subject, silently diverging from CPython (capture is a binding/alias). Affects both `case P() as q:` and bare `case q:`. No warning. The sibling mechanism already exists and works: an if/else branch-assigned local from the same sources hoists as borrow-form `P* x; x = &(p1);` (verified). The match hoisting path misses the borrow-form treatment. Gap-sweep note: only sync free-function bodies tested; resumable-frame match arms and union-typed subjects (which dispatch differently in gen_match) were not separately verified.
- **Evidence**: Repro: p = P(); match p: case P() as q: q.v = 9; print(p.v) -> TPy prints 0, CPython prints 9 (bare `case q:` identical). Generated C++: `P p = P(); std::optional<P> q; auto& __match_subject = p; { q = __match_subject; q->v = 9; } std::cout << p.v;`
- **Fix direction**: Hoist match-capture locals of non-value types in borrow form (P*) and bind via address-of the subject lvalue, mirroring the if/else hoisted-local path; _emit_binding's `escaped = subject_expr` assignment should become `escaped = &(subject_expr)` for pointer-hoisted names. Must consider union subjects (variant<A*,B*>) and the resumable (frame-field) branch at match.py:974+ which has its own value/frame_slot/pointer split.
- **Verifier adjustment**: Technically confirmed, severity adjusted down. Reproduced: p = P(); match p: case P() as q: q.v = 9; print(p.v) -> TPy 0, CPython 9. Generated C++ verified: 'std::optional<P> q; auto& __match_subject = p; { q = __match_subject; q->v = 9; }'. Code location confirmed: match.py _emit_binding lines 953-1001; the pre-declared path (lines 998-999) emits plain assignment into the hoisted std::optional<P>, while the non-hoisted path (line 1001) emits 'auto& {escaped} = {subject_expr};' which would al...

#### B25. await in a match-case guard emits '/* unknown expr */' into the C++ (build failure, no TPy diagnostic)

- **Location**: tpyc/parse/desugar_suspensions.py:124-149, tpyc/parse/desugar_suspensions.py:105-122
- **Severity / category**: medium / crash -- reproduced: yes -- found by `parser`
- **Problem**: desugar_suspensions claims ownership of all conditional/repeated suspension positions (module docstring, lines 19-28), but match-case guards -- a per-arm conditionally-evaluated position -- are missed entirely: _lower_stmt_exprs iterates dataclass fields that are TpyExpr or list[TpyExpr], and TpyMatch.cases is a list of TpyMatchCase objects, so case.guard is never visited (_recurse_sub_bodies handles only case.body). The codegen await-lifter then fails too, and the compiler emits literal '/* unknown expr */' into the generated C++, producing an opaque g++ error with no TPy-level diagnostic. Note TpyMatch.exprs() DOES include guards (nodes.py:1124-1129), so the desugarer's field-introspection traversal is strictly narrower than the AST's own walk contract -- the same mismatch pattern could bite any future expr slot stored inside a non-Expr child object (TpyWithItem was special-cased; TpyMatchCase was not).
- **Evidence**: /tmp/agents/matchguard2.py: async def go(n): match n: case 1 if await f(1): ... -> generated C++ contains `if (__match_subject == 1 && /* unknown expr */)` and the build fails with 'expected primary-expression before )'. No tpyc diagnostic.
- **Fix direction**: Either lower guard awaits (rewrite the match into an if/elif chain when any guard suspends, since each guard is only evaluated when the pattern matches and prior guards failed), or reject 'await in match-case guard' with a clear ParseError like the comprehension case at desugar_suspensions.py:187-194. Prefer driving the traversal off stmt.exprs()/sub_bodies() instead of raw dataclass-field introspection so guard-like slots can't be silently missed again.
- **Verifier adjustment**: Mechanism and repro confirmed: --dump-code on /tmp/agents/matchguard2.py (async fn with 'case 1 if await f(1):') emits 'if (__match_subject == 1 && /* unknown expr */)' at line 4141 of the dump -- invalid C++ guaranteed to fail the g++ build with no TPy diagnostic. Code confirms the traversal gap: desugar_suspensions.py _lower_stmt_exprs (124-149) visits only direct TpyExpr / list[TpyExpr] dataclass fields plus the TpyWithItem special case; _recurse_sub_bodies (105-122) special-cases TpyMatch...

### Theme: Ownership / move analysis: alias-blindness and liveness gaps  (worst: critical, 11 findings)

#### B26. Pointer-alias frame local reassigned to a fresh object stores a pointer to a dead stack slot -> segfault

- **Location**: tpyc/codegen_cpp/gen_async.py:1810-1918 (_classify_pointer_alias_locals), tpyc/codegen_cpp/gen_async.py:1041-1086 (frame field emit)
- **Severity / category**: critical / ub -- reproduced: yes -- found by `codegen-orchestration`
- **Problem**: _classify_pointer_alias_locals's docstring says it captures 'single-assign aliases', but the walk marks ANY TpyVarDecl of a plain non-value type whose init is a non-rvalue lvalue -- it never verifies the local is assigned exactly once. A local first bound as an alias (`b = items[0]`) and later reassigned to a fresh object (`b = Box(99)`) keeps the `T*` frame field; the reassignment emits `Box __slot_1 = Box(...); b = &__slot_1;` inside the switch case, so the frame pointer dangles as soon as the case block exits. Any read of `b` after the next suspension is UB. Applies to both generators and async defs (the same classifier seeds both via _resumable_frame_ctx).
- **Evidence**: Repro /tmp/agents/gen_alias_reassign2.py: `b = items[0]; yield b.v; b = Box(99); yield 0; yield b.v`. Generated C++ (case S_RESUME_0): `Box __slot_1 = Box(::tpy::BigInt(99)); b = &__slot_1; __state = S_RESUME_1; return ::tpy::BigInt(0);` then case S_RESUME_1: `return b->v;` reading through the dead pointer. Compiled binary: Segmentation fault (exit 139); CPython prints 1 / 0 / 99. Not tracked in BUGS.md (entries 25/32/42 cover tuple-literal copies and protocol-param aliases, not this).
- **Fix direction**: Require single-assignment (and no rvalue reassign on any path) before classifying a local as a pointer alias -- consult sema's reassigned_vars/flow facts; reassigned mixed alias/owning locals need either a frame_slot storage fallback (accepting the documented copy) or the dual-field shape sketched in BUGS.md:42, or a clean sema rejection. Sibling check for the gap-sweep: the same classifier feeds tuple-unpack borrow targets (s.is_ref) -- a reassigned unpack target has the same hole.

#### B27. list.append of a mutable pointer-form Optional param MOVES OUT of the caller's live object (warning says 'copies')

- **Location**: tpyc/codegen_cpp/expressions.py:418-424
- **Severity / category**: critical / miscompile -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: gen_expr_deref's borrow->storage conversion for a pointer-repr Optional source flowing into an Own[Optional[T]] sink (e.g. list.append's value param) emits `result ? std::optional<T>(std::move(*result)) : std::nullopt`. The std::move is unconditional: when the source is a MUTABLE borrowed param (T*), the caller's live object is gutted (vector/string members emptied) with no ownership transfer requested. The only diagnostic is a mild 'copies Box | None into owned storage; use copy()' warning -- the text claims a copy while the code steals. When the param happens to be const (no mutation in the body), std::move on the const lvalue degrades to a copy, so the bug is invisible until the function also mutates the param. This is the storage-form lift at the container-insert boundary applying move semantics to a non-Own borrow.
- **Evidence**: Repro /tmp/agents/bf/v_move_steal.py: `class Box: data: list[Int32] (init [1,2,3])`; `def stash(xs: list[Box | None], b: Box | None): b.data.append(4) if b is not None; xs.append(b)`; caller prints len(b.data) after stash. Generated: `void stash(std::vector<std::optional<Box>>& xs, Box* b) { ... xs.push_back(b ? std::optional<Box>(std::move(*b)) : std::nullopt); }`. Observed output: TPy prints 0 (data vector moved-from), CPython prints 4. tpyc diagnostic: 'warning: copies Box | None into owned storage; use copy() to make this explicit' -- describes a copy, code performs a move-out.
- **Fix direction**: In the Own[Optional]-sink branch, only emit std::move when the source is actually consumable (Own param being forwarded / last use of an owned local); for a borrowed pointer-form Optional emit a copy (matching the warning) -- `std::optional<T>(*result)`. Check the sibling sinks: dict value insert, set add, field store of Optional, and the Union analog (which currently doesn't convert at all, see container-store finding).

#### B28. Closure (nested def) capture-by-ref does not constrain auto-move after the def site

- **Location**: tpyc/liveness.py:308-315
- **Severity / category**: critical / miscompile -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: Liveness adds a nested def's captured_names to the live set only when the backward walk reaches the def statement, protecting uses BEFORE the def. Uses AFTER the def are processed earlier in the backward walk with the captured names absent, so a consuming use (Own[T] arg) after the def is marked last-use and codegen emits std::move while a by-reference lambda capture of the same variable is still callable. The closure then reads a moved-from object: silently wrong values (empty list/string), or panics/UB if the closure indexes the now-empty container. No diagnostic of any kind. Sound fix needs captured-by-ref names to stay live from the def to the end of the closure's possible lifetime (conservatively: function end), or exclusion of by-ref-captured vars from movable_locals. Sibling constructs to check in gap-sweep: lambdas (currently masked by by-value capture, see separate finding), nested defs inside async/generator frames.
- **Evidence**: Repro /tmp/agents/own/closure2.py:
  p = Point()           # Point holds items: list[Int32] = [1,2,3]
  def show(): print(len(p.items))
  s.consume(p)          # consume(self, p: Own[Point]) appends to self.stored
  show()
Generated C++:
  auto show = [&p]() { std::cout << ::tpy::__len__(p.items) << "\n"; };
  s.consume(std::move(p));
  show();
Observed output: 0 (CPython prints 3). Zero compiler diagnostics.
- **Fix direction**: In _analyze_stmt for TpyNestedDef (and in _compute_stmt_live_only), captured-by-ref names must be treated as live on every path reachable after the def (simplest: pre-scan the body for nested defs and seed their captured names into the initial live set / remove them from movable_locals), not only added at the def statement during the backward walk.

#### B29. Borrowed function-return alias is invisible to auto-move: source list moved into consuming callee while a local borrows an element -- SIGSEGV use-after-free

- **Location**: tpyc/prescan.py:210-213, tpyc/liveness.py:84-98, tpyc/sema/statements.py:115-159
- **Severity / category**: critical / ub -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: The alias map fed to analyze_last_uses only records simple name-to-name inits (TpyVarDecl with TpyName init, prescan.py:213). A local initialized from a borrowing call (n = first(xs), where first's return_borrows_from = {0}) is a C++ T& into xs's element storage, but it never enters alias_sources, so xs is still marked last-use at a later consuming use and auto-moved. Sema ALREADY computes the borrow fact (_register_call_result_borrow records n borrows-from xs in the BorrowTracker, used for iterator-invalidation checks), but the liveness/auto-move path never consults it. When the consuming callee genuinely consumes (stores then drops), the element storage is freed and the borrow dangles: hard SIGSEGV.
- **Evidence**: Repro /tmp/agents/own/borrowret2.py:
  def first(xs: list[P]) -> P: return xs[0]
  def drop(xs: Own[list[P]]) -> Int32:
      store: list[list[P]] = []; store.append(xs); return len(store)
  xs = [P(5)]
  n = first(xs)
  print(drop(xs))
  print(n.vals[0])
Generated C++: P& n = first(xs); ... drop(std::move(xs)); ... n.vals[0]
Observed: binary exits with SIGSEGV (exit 139), no output, no compiler diagnostic. CPython prints 1 then 5.
- **Fix direction**: Feed return_borrows_from-derived borrows (and BorrowTracker ELEMENT borrows generally) into the liveness alias suppression: a variable with a live borrower must not be auto-moved (fall back to copy+warning for copyable, error for @nocopy). This is the same hook _has_live_alias already implements for name aliases. Gap-sweep should check method calls and property getters too (both flow through _register_call_result_borrow).

#### B30. Field-access alias (a = o.inner) does not suppress auto-move of o: silent read of moved-from field

- **Location**: tpyc/prescan.py:210-213, docs/MOVE_SEMANTICS_DESIGN.md:325-326
- **Severity / category**: critical / miscompile -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: Phase 5 alias-aware liveness tracks only name-to-name aliases; the design doc explicitly defers field-access aliases ('not tracked (future work if needed)'). But the consequence today is a fully silent miscompile: 'a = o.inner' emits Inner& a = o.inner; a later consuming use of o auto-moves it (move ctor moves the inner field's buffers out), and reads through a observe the moved-from (empty) field. No warning, no error -- the 'conservative: if unsure, don't move' principle is violated because the analysis is sure for the wrong reason. Not listed in BUGS.md.
- **Evidence**: Repro /tmp/agents/own/aliasfield.py:
  o = Outer()          # o.inner.vals == [7, 8]
  a = o.inner
  h.take(o)            # take(self, o: Own[Outer]) appends
  print(len(a.vals))
Generated C++:
  Inner& a = o.inner;
  h->take(std::move(o));
  std::cout << ::tpy::__len__(a.vals) << ...
Observed output: 0 (CPython prints 2). Zero diagnostics.
- **Fix direction**: Track field-access aliases in prescan (alias of the ROOT object name suffices for soundness: a borrows o), so _has_live_alias suppresses the move. Until implemented, file in BUGS.md Safety section -- the design-doc deferral note understates that this is an active unsound-move source, not just a missed optimization.
- **Verifier adjustment**: Finding is real exactly as described; only severity is corrected high -> critical. Re-ran /tmp/agents/own/aliasfield.py: generated 'Inner& a = o.inner; h->take(std::move(o)); ... __len__(a.vals)'; binary prints 0, CPython prints 2, zero diagnostics. docs/MOVE_SEMANTICS_DESIGN.md:325-326 confirms the deferral ('Field-access aliases ... not tracked (future work if needed)'); I verified no BUGS.md entry covers the unsound-move consequence (BUGS.md:162 is an unrelated const-binding issue, C++-cau...

#### B31. Live generator object borrowing its iterable param does not suppress auto-move of the source: generator silently iterates moved-from container

- **Location**: tpyc/sema/analyzer.py:1398-1404, tpyc/prescan.py:210-213
- **Severity / category**: critical / miscompile -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: A generator's frame stores non-value params by reference, and analyzer.py even records this in return_borrows_from ('the returned struct stores non-value params as T& references ... so the result borrows from those params'). But g = gen(xs) creates an alias of xs through a call result, which the liveness alias map cannot represent (name-to-name only), so a later consuming use auto-moves xs while the un-exhausted generator g still references it. Iterating g then walks a moved-from (empty) vector: silently truncated/empty iteration, no diagnostic. Sibling of the closure-capture finding (generator frame = the other borrow-holding object liveness ignores).
- **Evidence**: Repro /tmp/agents/own/genborrow2.py:
  def gen(xs: list[Int32]) -> Iterator[Int32]:
      for x in xs: yield x
  def drop(xs: Own[list[Int32]]) -> Int32:
      store: list[list[Int32]] = []; store.append(xs); return len(store)
  xs = [1, 2, 3]
  g = gen(xs)
  print(drop(xs))      # xs auto-moved
  for v in g: print(v)
Observed output: '1' only -- the for loop prints nothing (CPython prints 1, then 1 2 3). Zero diagnostics.
- **Fix direction**: Same hook as the borrowed-return finding: a call whose return_borrows_from includes param i must register the result variable as an alias of the argument for liveness purposes. Generators set the fact at analyzer.py:1398-1404 already; it just is not consumed by auto-move. Gap-sweep: async coroutine objects held across a consume of their argument, and simple-generator lambda peephole captures.
- **Verifier adjustment**: Finding is real as described; severity corrected high -> critical, plus one description supplement. Verified analyzer.py:1396-1404 sets gen_borrows into return_borrows_from for non-value/str generator params, and the alias map (prescan.py:210-213, name-to-name only) cannot represent the call-result alias 'g = gen(xs)'. Re-ran /tmp/agents/own/genborrow2.py: generated 'auto g = gen(xs); std::cout << drop(std::move(xs))...'; binary prints only '1' (the for loop over g is silently empty) where CP...

#### B32. Assign-statement target reads processed after value reads: move in RHS, read of moved-from var in subscript target of the same statement

- **Location**: tpyc/liveness.py:254-279, tpyc/liveness.py:702-735
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: For TpyAssign (and TpyAugAssign), liveness calls _process_reads separately for stmt.value and for stmt.target.obj/index. The per-call name_counts dedup (which suppresses marking when a name occurs multiple times in ONE expression because C++ evaluation order is unspecified) therefore does not see that the same variable is read in both the value and the target. The value occurrence is marked last-use before the target reads are added to live, so codegen emits e.g. tpy::__setitem__(d, __len__(b.items), k->take(std::move(b))) -- function args are indeterminately sequenced, and g++ evaluates the consuming call first, so the key/index reads the moved-from object. Silent wrong result, no diagnostic.
- **Evidence**: Repro /tmp/agents/own/sameexpr2.py:
  b = Blob()                  # items = [10,20,30]
  d[len(b.items)] = k.take(b) # take(self, b: Own[Blob]) stores b, returns 99
Generated C++:
  ::tpy::__setitem__(d, ::tpy::__len__(b.items), k->take(std::move(b)));
Observed output: {0: 99} (CPython prints {3: 99}). Zero diagnostics.
- **Fix direction**: Collect reads of the whole statement (value + target sub-expressions) into a single _process_reads pass so the multi-occurrence suppression fires, or explicitly suppress last-use marks for names that also appear in the assignment target. Check the sibling: TpyAugAssign has the identical split (lines 267-279), and field-target assigns (obj.f = consume(obj)) share the pattern.

#### B33. _analyze_with: context-manager variable can be auto-moved inside the with body; __exit__ runs on moved-from object

- **Location**: tpyc/liveness.py:476-492
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: _analyze_with's docstring claims '__exit__ runs after the body on every path but reads only the context manager, not body locals' -- yet the body is analyzed with the context-manager name NOT in the live set (the context_expr reads are processed only after the body, protecting statements before the with). So a consuming use of the manager variable inside its own with body is marked last-use and moved; the epilogue then calls __exit__ on the moved-from object. Silent wrong behavior in __exit__ (e.g. releases nothing / logs empty state), no diagnostic.
- **Evidence**: Repro /tmp/agents/own/withmove.py:
  g = Guard()          # g.vals == [1, 2]; __exit__ prints len(self.vals)
  with g:
      k.take(g)        # take(self, g: Own[Guard])
Generated C++:
  auto& __ctx_1 = g; __ctx_1.__enter__();
  try { k->take(std::move(g)); __ctx_1.__exit__({}, nullptr, {}); } ...
Observed output: 'exit sees 0' (CPython prints 'exit sees 2'). Zero diagnostics.
- **Fix direction**: In _analyze_with, seed the live set for the body walk with the context-manager root names (and only kill them after the body, since __exit__ reads them on every path) -- mirroring how _analyze_try keeps handler/finally reads live across the try body. Check sibling: async with shares whatever lowering path consumes these marks.

#### B34. Ternary of two lvalues passed to Own[T] param: no sema diagnostic, emits ill-formed C++ (lvalue bound to T&&)

- **Location**: tpyc/sema/compatibility.py:928-933, tpyc/sema/compatibility.py:1556-1596
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: The Own[T]-coercion copy/move logic only fires when is_lvalue(source_expr) is true; a conditional expression selecting between two lvalues (a if cond else b) is classified as neither lvalue (no copy warning, no copy() requirement) nor handled as an rvalue temporary, so codegen passes the raw `(cond ? a : b)` -- a C++ lvalue -- to the `P&&` Own parameter. The result is a g++ 'cannot bind rvalue reference to lvalue' error leaking to the user with zero TPy diagnostics. Plausible Python (choose which object to hand off).
- **Evidence**: Repro /tmp/agents/own/ternary.py:
  k.take(a if cond else b)   # take(self, p: Own[P])
tpyc emits no warnings/errors; generated C++:
  k->take(((cond) ? (a) : (b)));
with signature void K::take(P&& p); g++: 'cannot bind rvalue reference of type P&& to lvalue ... initializing argument 1 of void K::take(P&&)'.
- **Fix direction**: Classify ternary-of-lvalues as lvalue in is_lvalue (recursing into both arms), which routes it into the existing copy-warning/copy path; optionally add per-arm last-use moves later (codegen could emit cond ? std::move(a) : std::move(b) when both arms are at last use). Gap-sweep siblings: walrus expressions and parenthesized named expressions feeding Own slots.

#### B35. _validate_consuming_call only gates TpyName/TpyFieldAccess receivers -- subscript receivers fall through to a cryptic C++ '&&'-qualifier error

- **Location**: tpyc/sema/methods.py:914-962
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-methods-protocols`
- **Problem**: Consuming methods (self: Own[Self]) are validated only for TpyFieldAccess (rejected) and TpyName (local-var rules) receivers; every other expression shape -- subscript (boxes[0].take()), tuple element, parenthesized lvalue chains -- is implicitly treated as a 'temporary' and accepted by sema. The only backstop is that codegen emits the consuming method &&-ref-qualified, so g++ rejects lvalue receivers with 'passing Box<int> as this argument discards qualifiers' deep in template notes, instead of the clean per-shape diagnostics the function gives for fields/self/pointers. If any future receiver shape yields an rvalue reference or a by-value subscript, the move-out-of-container would compile silently.
- **Evidence**: Repro:
  boxes = [Box(Int32(1)), Box(Int32(2))]
  v = boxes[0].take()
Sema: exit 0, generated 'int32_t v = ::tpy::__getitem__(boxes, 0).take();'.
C++ build: 'error: passing tpystd::tplib::box::Box<int> as this argument discards qualifiers ... in call to tpy::own_return_t<T> Box<T>::take() &&'.
- **Fix direction**: Add an explicit arm for TpySubscript (and a default-deny for any receiver expression that is an lvalue projection into owned storage) with the same wording as the field rejection: consuming methods cannot be called on container elements; suggest pop()/swap-out. Keep call-result temporaries allowed. Gap-sweep: check the auto_own consuming clone selection path and Optional-unwrapped receivers for the same shapes.

#### B36. Tuple-unpack targets are never movable: consuming an unpacked local at last use forces a copy plus warning

- **Location**: tpyc/prescan.py:221-230, tpyc/sema/analyzer.py:1421-1430
- **Severity / category**: low / perf -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: Locals bound by tuple unpack (a, b = P(1), P(2)) never enter ever_owned_locals/movable_locals, so a consuming use at last use emits 'copies P into owned storage' and a deep copy, where the identical plain-decl form (b = P(2); k.take(b)) auto-moves silently. Inconsistent ownership inference between the binding forms; the user is pushed to write copy() for a value that is demonstrably dead. Adjacent to (but distinct from) the tracked BUGS.md entry about named-source @nocopy tuple unpack failing the C++ build.
- **Evidence**: Repro /tmp/agents/own/tupref.py:
  a, b = P(1), P(2)
  k.take(a)   # line 26 -- legitimately not last use (a read later)
  k.take(b)   # line 27 -- IS last use, still warns + copies
Diagnostics: 'tupref.py:27: warning: copies P into owned storage; use copy() to make this explicit' (b is never used afterwards).
- **Fix direction**: Mark unpack targets whose elements are distributed by value as owned locals (prescan/ever_owned_locals), so the standard last-use auto-move applies. Coordinate with the existing BUGS.md owned-tuple-unpack work (the rvalue-source whole-tuple slice already creates movable owned locals -- extend to the general unpack-binding case).

### Theme: Borrow form vs storage form: unhandled boundaries  (worst: high, 9 findings)

#### B37. Generator/genexpr yield of a concrete Union element silently copies -- mutation through the yielded value is lost (no diagnostic)

- **Location**: tpyc/typesys.py:1610-1630, tpyc/codegen_cpp/gen_generators.py:530-563
- **Severity / category**: high / silent-copy -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: yield_uses_borrow_slot excludes UnionType claiming Optional/Union 'keep their own representation (pointer variants)' (docstring, and GENERATOR_YIELD_ABI_DESIGN.md says the same), but _iter_slot_for_yield then falls through to the bare VALUE form: the iterator slot is std::variant<A,B> and the yield emits `auto __val = v;` -- a full copy of the element. The consumer's isinstance-narrowed mutation writes the copy; CPython (and TPy's own direct for-loop over the same list, and TPy's concrete reference-type yields after the yield-ABI fix) alias. Both emitters are affected: the simple-generator lambda peephole AND the resumable frame (`__next__` returns std::expected<std::variant<A,B>, StopIteration>`), and generator expressions (`make_generator<std::variant<A,B>>`). Zero warnings emitted. The Optional sibling is differently broken (BUGS.md:251 -- consumer narrowing C++ build fail), so neither pointer-repr exclusion of the yield-ABI gate actually has a working aliasing path; Union is the silent one.
- **Evidence**: Repro /tmp/agents/bf/c_union_yield.py: `def each(xs: list[A | B]) -> Iterator[A | B]: for v in xs: yield v`; consumer `for v in each(xs): if isinstance(v, A): v.x = 99` then reads xs[0].x. Generated peephole: `auto __val = v; return std::optional<std::variant<A, B>>(__val);`. Observed: TPy prints 1, CPython prints 99. Resumable variant (/tmp/agents/bf/s_union_yield_resumable.py, two yields): `std::expected<std::variant<A, B>, ::tpy::StopIteration> __next__()` -- same value-form copy. Genexpr (/tmp/agents/bf/c2_genexpr_union.py): `make_generator<std::variant<A, B>>`. tpyc reports 0 warnings.
- **Fix direction**: Give Union yields the pointer-variant borrow slot (std::variant<A*,B*>) the way function params/returns already do, with the same durable-source/fresh-dangle sema gates the bare-reference yield ABI uses; or at minimum emit the silent-copy warning. Must be fixed together with BUGS.md:251 (Optional yield) -- same gate, sibling representations. Gap-sweep: check async generators (`async for`) for the same exclusion.

#### B38. make_union drops force_pointer_repr -> ill-formed C++ for `x = c.get() if flag else None`

- **Location**: tpyc/typesys.py:3389-3391, tpyc/typesys.py:3417, tpyc/typesys.py:1777
- **Severity / category**: high / rejects-valid -- reproduced: yes -- found by `typesys`
- **Problem**: OptionalType.force_pointer_repr is the load-bearing ABI commitment that a generic's Optional return is T* even for value-typed T (the class docstring at typesys.py:2616-2624 explicitly warns 'Silently dropping the flag during any structural transform miscompiles any v = container.get() pattern'). make_union violates exactly this invariant: when flattening it does `flat.append(t.inner)` for an OptionalType member (line 3391) and reconstructs with a fresh `OptionalType(deduped[0])` (line 3417), losing the flag. Any flow-merge that re-derives a union from a force-pointer-repr Optional (ternary with None, if/else branch join) re-types the local as std::optional<T> while the rvalue is T*, with no ptr_to_optional bridge emitted -- the generated C++ does not compile. `_make_marker`'s Optional distribution (line 1777, `OptionalType(_make_marker(inner.inner, ...))`) is a second drop site, also confirmed. Sibling constructs to check in gap-sweep: union_none_narrow / narrowing rejoin paths, and any other site constructing OptionalType(x) from an existing Optional instead of using with_inner().
- **Evidence**: Repro: class Container[T] with `def get(self) -> T | None: return self.v`; `c = Container(Int32(5)); w = c.get() if flag else None`. Generated C++: `std::optional<int32_t> w = ((flag) ? (std::optional<int32_t>(c.get())) : ...)` where `c.get()` returns `int32_t*`. g++ 14: "error: no known conversion for argument 1 from 'int*'" (no viable std::optional<int> ctor). API confirmation: make_union(OptionalType(INT32, force_pointer_repr=True), VoidType()) returns OptionalType with force_pointer_repr=False; _make_marker(o, 'Send', lenient=True) likewise returns flag=False.
- **Fix direction**: In make_union's flatten loop, remember whether any flattened OptionalType carried force_pointer_repr and reconstruct the single-member case via OptionalType(t, force_pointer_repr=flag) (or route through with_inner of the original Optional). Same for _make_marker's Optional branch (use inner.with_inner). Longer-term this is the docstring's own argument for replacing the flag with a structural representation in the THIR migration.
- **Verifier adjustment**: Fully reproduced, and the finding understates the impact. (1) As claimed: generic Container[T] with `get() -> T | None` emits `T* get()`; `w = c.get() if flag else None` re-types w via make_union as std::optional<int32_t> and emits `std::optional<int32_t>(c.get())`; g++ 14 fails with 'no known conversion ... from int*' (verified by full build). API check confirms make_union(OptionalType(INT32, force_pointer_repr=True), VoidType()) returns OptionalType with force_pointer_repr=False; code at ty...

#### B39. isinstance-narrowed union LOCAL across a suspension emits std::get on the frame_slot without dereferencing -> C++ build error on valid code

- **Location**: tpyc/codegen_cpp/gen_async.py:2976-3002 (_emit_resume_narrowings) -> tpyc/codegen_cpp/statements.py:_emit_isinstance_extractions (sibling file)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-orchestration`
- **Problem**: A union-typed local hoisted into the resumable frame is stored as `tpy::frame_slot<std::variant<...>>`. When an isinstance-narrowed region contains a suspension, the (re-)established narrowing binding is emitted as `auto& __y = std::get<Dog>(y);` against the frame_slot itself -- missing the `*y` deref -- so plausible code fails the C++ build with a g++ error leaking to the user (no TPy diagnostic). The narrowed-union PARAM case (pointer-variant `std::variant<A*,B*>`) works; only frame_slot-stored locals are broken. This affects the initial in-case emission too (the S_INITIAL case in the dump shows the same un-dereffed std::get), so any `if isinstance(local_union, X): ... await ...` is uncompilable.
- **Evidence**: Repro /tmp/agents/async_narrow_simple.py: `y: Dog | Cat = Dog(); if isinstance(y, Dog): await asyncio.sleep(0); print('dog', y.n)`. g++: "'tpy::frame_slot<std::variant<Cat, Dog>>' is not derived from 'const std::variant<_Types ...>'" at `auto& __y = std::get<Dog>(y);`. CPython prints `dog 1`. Param variant (/tmp/agents/async_narrow_param.py) compiles and runs correctly.
- **Fix direction**: The extraction emitter must route the narrowed name through the frame storage access (generator_storage_name / frame_slot deref `(*y)`) like every other read of a frame_slot local -- the narrowing path bypasses the in-generator-body name rewrite. Sits in statements.py (outside my assigned files); flagging for the statements/narrowing reviewer.

#### B40. Optional-of-tuple (`tuple[..., Ref] | None`) has no coherent form: param, call-arg, return-binding and body access each pick a different shape -> ill-formed C++ in every direction

- **Location**: tpyc/typesys.py:2674-2697, tpyc/typesys.py:3217-3218
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: TupleType.is_value_type() always returns True, so OptionalType.uses_pointer_repr() is False for `tuple[Int32, Box] | None` -> params/returns render storage form std::optional<std::tuple<int,Box>> (also pass-by-value, a silent copy even if it compiled). But the call-site arg emits the borrow tuple std::tuple<int,Box*> (not even optional-wrapped), the body's subscript access emits pointer deref (std::get<1>(*t)->val), and a local bound from such a return takes the wraps_pointer_repr_tuple borrow shape std::optional<std::tuple<int,Box*>> with no converter from the storage-form return. Every combination is a raw g++ error with no TPy diagnostic. This kills the canonical Python idiom `-> tuple[...] | None` for any tuple containing a reference type. Untracked in BUGS.md (the nested-tuple entries at BUGS.md:226/239 are a different shape).
- **Evidence**: Repro A /tmp/agents/bf/a_opt_tuple.py: param `t: tuple[Int32, Box] | None` -> signature `void f(std::optional<std::tuple<int32_t, Box>> t)`, body `std::get<1>((*t))->val = 99;` (-> on Box, g++ error), call `f(std::tuple<int32_t, Box*>{1, &(b)})` (no conversion to optional<tuple<int,Box>>, g++ error). Repro B /tmp/agents/bf/b_opt_tuple_ret.py: method `-> tuple[Int32, Box] | None` returns `std::optional<std::tuple<int32_t, Box>>` copying self.pair; caller binds `std::optional<std::tuple<int32_t, Box*>> r = std::optional<std::tuple<int32_t, Box*>>{h.find(true)};` -- g++ cannot convert. Both confirmed as C++ build failures with zero TPy diagnostics.
- **Fix direction**: Decide one borrow form for pointer-repr-element Optional-of-tuple (natural: std::optional<std::tuple<..., T*>> at params/locals, per-element tuple_to_storage/tuple_to_pointer under an optional map at the boundary; storage form std::optional<std::tuple<..., T>> at fields/returns with the lift at binding). Requires OptionalType.uses_pointer_repr/wraps_pointer_repr_tuple to drive param and call-arg emission consistently, not just locals. Sibling check for gap-sweep: Union containing a tuple member, and Own[tuple | None].
- **Verifier adjustment**: Factually confirmed in every direction claimed, but severity recalibrated to medium per the rubric ('rejects-valid on reasonable code' is medium; nothing here ever runs, so no miscompile/UB ships). typesys.py:3217-3218 TupleType.is_value_type()==True and OptionalType.uses_pointer_repr() (2674-2685) confirm the storage-form choice; wraps_pointer_repr_tuple (2687-2697) documents the borrow-form local shape. Repro /tmp/agents/verify/f4a_opt_tuple.py: param `std::optional<std::tuple<int32_t, Box>...

#### B41. await result of `async def -> T | None` (pointer-repr Optional) is unconverted: storage-form Poll payload assigned to pointer-form binding; async return also copies where the sync sibling aliases

- **Location**: tpyc/codegen_cpp/gen_async.py:634-635, tpyc/codegen_cpp/gen_async.py:3596-3603
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: _ret_cpp uses type_to_cpp (storage form: Poll<std::optional<Box>>) for the coroutine's return slot, while a sync `def -> Box | None` returns pointer form via to_cpp_return (optional_to_ptr, aliasing the field -- verified in repro R). The await-result ASSIGN/VARDECL emit (`target = std::move(__rN).value();`) does no form conversion, so binding the result to the consumer's pointer-form local/frame-field (Box* t) is ill-formed C++ (cannot convert std::optional<Box> to Box*). No TPy diagnostic. Even with a mechanical optional_to_ptr inserted, the pointer would target the moved temporary (dangle), and the design-level asymmetry remains: `t = h.get()` aliases h.opt for sync but the async twin returns a copy -- divergent aliasing semantics between `def` and `async def` for the identical body.
- **Evidence**: Repro /tmp/agents/bf/x2_await_opt.py: `async def get(h: H) -> Box | None: await asyncio.sleep(0); return h.opt` + `t = await get(h); t.val = 99`. Generated: `::tpystd::tpy::Poll<std::optional<Box>> __coro_get::__poll__ ... std::optional<Box> __tpy_async_ret = h.opt; return Poll<...>::ready(std::move(__tpy_async_ret));` and consumer frame `Box* t; ... t = std::move(__r0).value();`. g++: cannot convert 'std::optional<Box>' to 'Box*' in assignment. Sync control (/tmp/agents/bf/r_ret_opt_field.py) correctly emits `inline Box* H::get() { return ::tpy::optional_to_ptr(this->opt); }` and aliases.
- **Fix direction**: Align the async return convention for pointer-repr Optional with the sync one (Poll<Box*>), with the existing storage-root provenance gate for what may be returned as a borrow; or keep storage form and insert ptr-lift into a frame-held slot at the await binding. Gap-sweep: Union-returning async defs, async generators' yield slots, and Task[T|None] results likely share the gap.

#### B42. Container-element store boundary lacks the borrow->storage conversion: `xs[i] = p` (Union and Optional) and `xs.append(p)` (Union) emit unconverted borrow forms

- **Location**: tpyc/codegen_cpp/statements.py:2180-2224, tpyc/codegen_cpp/expressions.py:414-431
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: The subscript-assign path applies only _maybe_wrap_tuple_to_storage; it has no ptr_to_optional / to_value_variant step, so storing a borrow-form union param (variant<A*,B*>) or pointer-form Optional param (T*) into a container element (vector<variant<A,B>> / vector<optional<T>>) emits a direct assignment that g++ rejects. push_back of a union has the same hole (the gen_expr_deref Own-sink conversion branch handles OptionalType only, no UnionType analog). Inconsistent with the siblings that DO convert: field store of a union (`h.pet = p` -> to_value_variant, statements.py:2320), container literals (expressions.py:4409 _to_value_variant_if_needed), and append-of-Optional (which 'works' only via the buggy std::move branch -- see the move-out finding). No TPy diagnostic; raw g++ errors.
- **Evidence**: Repro /tmp/agents/bf/y2_siblings.py. Generated: `void put(std::vector<std::variant<A, B>>& xs, const std::variant<A*, B*> p) { ::tpy::__setitem__(xs, 0, p); }` -> g++ 'no match for operator=' variant<A,B> = variant<A*,B*>; `xs.push_back(p)` -> 'no matching function ... push_back(const std::variant<A*,B*>&)'; `::tpy::__setitem__(xs, 0, b)` with b: const A* into vector<optional<A>> -> 'no match for operator=' optional<A> = const A*. Field store in the same file correctly emits `h.pet = ::tpy::to_value_variant<std::variant<A, B>>(p);`.
- **Fix direction**: Route subscript-assign and method-call value args through the same dest-shape dispatch the field-store/container-literal paths use (ptr_to_optional / to_value_variant / tuple_to_storage in one converter keyed by destination storage type). Gap-sweep: dict value assignment d[k]=p, set.add, insert(), and dict.setdefault likely share whichever holes setitem/append have.
- **Verifier adjustment**: Core claim fully reproduced; one description detail is wrong. statements.py:2180-2224 confirms the subscript-assign path applies only _maybe_wrap_tuple_to_storage (line 2190), and expressions.py:418-424 confirms the Own-sink conversion handles OptionalType only (no UnionType analog). Repro /tmp/agents/verify/f6_siblings.py generated: `::tpy::__setitem__(xs, 0, p)` (variant<A*,B*> into vector<variant<A,B>>), `xs.push_back(p)` (same), `::tpy::__setitem__(xs, 0, b)` (const A* into vector<optiona...

#### B43. Walrus binding from a storage-form Optional source misses the optional_to_ptr lift the plain-assignment path has

- **Location**: tpyc/codegen_cpp/expressions.py:5967-6033
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: _gen_named_expr declares the walrus target in pointer form for pointer-repr Optionals (`Box* t = nullptr`) and lifts storage-form TUPLE sources via tuple_to_pointer, but has no Optional analog: the assigned value for a storage-form Optional source (field h.opt -> std::optional<Box>) is emitted unconverted into the T* target. Plain assignment (`t = h.opt`) correctly emits `Box* t = ::tpy::optional_to_ptr(h.opt);` (verified control). So the mainstream idiom `if (t := obj.attr) is not None:` fails the C++ build with no TPy diagnostic. Distinct from BUGS.md:285 (walrus REASSIGNMENT of non-value locals rejected at sema) -- this is the supported fresh-binding path.
- **Evidence**: Repro /tmp/agents/bf/d_walrus_opt.py: `if (t := h.opt) is not None: t.val = 99` -> generated `Box* t = nullptr; if (((t = h.opt) != nullptr))` -> g++ 'cannot convert std::optional<Box> to Box* in assignment'. Control /tmp/agents/bf/d2_control.py (plain `t = h.opt`) compiles with optional_to_ptr.
- **Fix direction**: In _gen_named_expr, when value_type is pointer-repr Optional and ctx.is_storage_form_optional_source(expr.value), wrap value_code in ::tpy::optional_to_ptr(...) -- same predicate the VarDecl path uses. Sibling to check: walrus from a storage-form UNION source (currently shielded by the 'isinstance() first argument must be a variable name' restriction, but reachable via `is not None`-style uses).

#### B44. Ternary joining a borrow-form arm and a storage-form arm (Optional and Union) emits mixed-type C++ ?: operands

- **Location**: tpyc/codegen_cpp/expressions.py:6192-6200, tpyc/codegen_cpp/expressions.py:6102-6190
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: _ptr_optional_branch returns the branch code unchanged whenever the branch type is OptionalType, assuming pointer form -- but a storage-form Optional source (field) renders as std::optional<T>, so `p if c else h.opt` emits `(c) ? (p) : (h.opt)` with operands Box* and std::optional<Box> (ill-formed ?:). The Union sibling has the same hole one level up: the whole ternary is wrapped in to_ptr_variant but the operands are variant<A*,B*> vs variant<A,B>, ill-formed before the helper is reached. _gen_if_expr already special-cases the analogous string_view/std::string arm mismatch, so the precedent exists; Optional/Union arms were missed. No TPy diagnostic.
- **Evidence**: Repro /tmp/agents/bf/e_ternary.py: `t = p if c else h.opt` (p: Box | None param, opt: field) -> `Box* t = ((c) ? (p) : (h.opt));` -> g++ 'operands to ?: have different types Box* and std::optional<Box>'. Union repro /tmp/agents/bf/e2_ternary_union.py -> `std::variant<A*, B*> t = ::tpy::to_ptr_variant(((c) ? (p) : (h.pet)));` -- ill-formed ?: of variant<A*,B*> and variant<A,B>.
- **Fix direction**: Per-arm form normalization: in _ptr_optional_branch, consult is_storage_form_optional_source and wrap with optional_to_ptr; for unions apply to_ptr_variant per arm instead of around the whole ternary. Same per-arm rule belongs in any future join point (e.g. `or`-chains if those ever produce non-value joins).

#### B45. frame_traits.local_slot classifies borrow-alias pointer frame slots as owned -- Send inference out of sync with codegen

- **Location**: tpyc/sema/frame_traits.py:111-129, tpyc/sema/analyzer.py:1192-1197
- **Severity / category**: medium / unsound-safety -- reproduced: no (code-read evidence) -- found by `sema-methods-protocols`
- **Problem**: Commit 40ff1508e changed codegen so borrow-form locals that survive a suspension (single-assign aliases like 'a = obj.field' / 'a = xs[0]', and tuple-unpack borrow targets) are stored as raw 'T*' frame fields (gen_async._classify_pointer_alias_locals -> pointer_form_names, emitted as 'T* name = nullptr;'). But sema's Send/Sync frame classifier local_slot only treats is_loop_var slots (frame_loop_var_names, sourced solely from pending_loop_vars, i.e. for-loop vars) as pointer slots; every other non-value local takes the 'owned tpy::frame_slot<T> storage -> T's traits' path. A generator/coroutine frame holding a T* alias into storage that is not itself a frame slot (module global, channel-call borrow tuple) can therefore be classified Send while it carries a raw pointer to originating-thread memory. The module docstring's storage model ('hoisted non-value locals: frame_slot<T> owned storage') predates the codegen change. Mitigating: the common alias sources are borrowed params, which already make the frame non-Send; enforcement surface (Send[...] slot stores, _value_frame_traits) is currently narrow -- hence medium, not high.
- **Evidence**: frame_traits.py:111-129: only 'if is_loop_var: return FrameSlot(name, False, False, ltype)'; statement-level aliases fall to '_value_slot_traits(inner)' (owned traits). analyzer.py:1194-1197 builds frame_loop_var_names exclusively from pending_loop_vars (populated only at statements.py:2520/2540 for for-loops). codegen gen_async.py:1045/1071-1085 emits 'const? T* name = nullptr;' for pointer_form_names which now includes _classify_pointer_alias_locals(func) (commit 40ff1508e: 'a borrow-form local aliasing a stable source must stay a T* frame slot across a suspension').
- **Fix direction**: Mirror the codegen classifier in sema: tag borrow-alias locals (the same single-assign-of-lvalue / unpack-target conditions _classify_pointer_alias_locals uses) on the AST or in a sema-side fact, and have local_slot classify them like loop vars (non-Send/non-Sync). Per CLAUDE.md THIR guidance, the borrow-form-local fact should be decided once in sema and consumed by both codegen and frame_traits, not re-derived in codegen.

### Theme: Silent copies where CPython aliases  (worst: critical, 11 findings)

#### B46. Using the items()-unpacked loop variable after the loop dereferences a never-assigned std::optional (UB, silent wrong output)

- **Location**: runtime/cpp/include/tpy/ordered_map.hpp:139-142, tpyc/codegen_cpp/statements.py (for-unpack lowering; hoisted-local shadowing), BUGS.md:257 (related non-unpack entry)
- **Severity / category**: critical / ub -- reproduced: yes -- found by `hunt-silent-copy`
- **Problem**: When the unpacked loop var of a `for k, v in d.items()` loop is read after the loop (legit Python: loop vars leak; 'last element' idiom), codegen hoists `std::optional<std::vector<int32_t>> v;` before the loop but the in-loop binding declares a SHADOWING `auto&& v` that never writes the hoisted local. The post-loop read emits `(*v)` on the still-disengaged optional -- undefined behavior. Observed: silently prints 0 where CPython prints 3. BUGS.md:257 tracks the plain `for x in items` shadow-instead-of-rebind divergence (stale value); this unpack sibling escalates it to UB because the hoisted local is an optional that is never engaged at all.
- **Evidence**: Repro: d["a"] = [1, 2, 3]; for k, v in d.items(): pass; print(len(v)) -> TPy prints 0 (CPython: 3). Generated C++: `std::optional<std::vector<int32_t>> v;` hoisted; loop body declares `auto&& v = ::tpy::unwrap_ref(...)` (shadow, hoisted v never assigned); post-loop `std::cout << ::tpy::__len__((*v))` derefs the empty optional.
- **Fix direction**: Same root as BUGS.md:257: the loop-var binding must write the hoisted function-scope local (rebind) instead of declaring a shadowing C++ local; for the unpack case the hoisted slot should be borrow-form (T*) assigned per iteration, which also depends on fixing the by-value items iterator (previous finding). Gap-sweep should check enumerate/zip unpack vars used post-loop too.

#### B47. for-loop variable shadowing an existing local: post-loop reads see the pre-loop value (CPython divergence, no diagnostic)

- **Location**: tpyc/prescan.py:243-244, tpyc/parse/parser.py:3246-3251
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `parser`
- **Problem**: In CPython, `for x in ...` rebinds an existing local x; after the loop x holds the last element. TPy compiles the loop variable as a fresh C++ loop-scope variable that shadows the existing local, so post-loop reads observe the stale pre-loop value. No warning or error is emitted. In prescan.py _scan_stmts, TpyForEach only does `declared.add(stmt.var)` -- it never marks the var as reassigned when it is already in `declared`, so downstream sema/codegen (hoist_loop_var / variable-model decisions) never learn the loop var aliases an existing binding. Parser side, TpyForEach carries no fact that var was pre-bound. This is a flow-fact gap in my assigned prescan; the consumer (sema/codegen loop emission) is outside my assignment -- gap-sweep should trace whether hoist_loop_var handles the 'used after loop AND pre-declared' case.
- **Evidence**: /tmp/agents/loopvar.py: def main(): x = 100; for x in range(3): pass; print(x). CPython prints 2; `uv run tpy /tmp/agents/loopvar.py` compiles silently and prints 100.
- **Fix direction**: In _scan_stmts, when stmt.var is already in `declared`, add it to result.reassigned (and rvalue_reassigned) so the loop var maps onto the existing binding (or is hoisted), mirroring CPython rebinding; alternatively emit a sema diagnostic until the variable model supports it. Verify the sibling constructs: comprehension loop vars (which CPython scopes separately, so shadowing there is CORRECT), `with ... as x` (prescan does mark reassigned -- prescan.py:245-252), and tuple-unpack for-loops (synthetic __for_tup var plus TpyTupleUnpack, which DOES mark reassignment, so the tuple form may behave differently from the scalar form).

#### B48. Simple-generator peephole captures non-value init-local aliases by value: aliased container silently copied at construction

- **Location**: tpyc/codegen_cpp/gen_generators.py:917-948 (_build_capture_list)
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `codegen-orchestration`
- **Problem**: _build_capture_list captures params by reference when non-value, but ALL init-statement VarDecl locals by value -- including a local that is an alias of a reference-type param (`xs = items` emits `std::vector<T>& xs = items;` in the factory, then captures `[..., xs, ...]` by value, copying the whole container into the lambda). Mutations of the source between generator construction and exhaustion are invisible to the generator -- CPython aliases. The resumable-frame path forwards such aliases to the captured param correctly, so this is another peephole-vs-frame semantic split. BUGS.md:32 tracks this for static-protocol params only ('[LOW] ... aliasing a static-protocol param'); plain containers (list/dict/set/class params) hit the same by-value capture, so the entry's scoping (and LOW rating) understates it. A borrow-checker warning does fire ('Mutation of items while borrowed (append may invalidate references)') but it claims invalidation while the actual behavior is a stale copy, and it only fires when the mutation is visible in the same module.
- **Evidence**: Repro /tmp/agents/gen_alias_capture.py: `def gen(items: list[int]): xs = items; i = 0; while i < len(xs): yield xs[i]; i += 1` with `g = gen(items); items.append(3); for x in g: ...`. Generated: `std::vector<::tpy::BigInt>& xs = items; ... [&items, xs, i]() mutable ...` -- xs captured by value. TPy prints 1 2; CPython prints 1 2 3.
- **Fix direction**: Capture by reference (&name) any init local whose declared type is non-value AND whose C++ emission is a reference binding to a param/longer-lived lvalue (the alias case); by-value capture remains correct for owning locals (acc = []) since the factory frame dies at return. Alternatively exclude alias-shaped init locals from is_simple_generator. Update BUGS.md:32's scope (not protocol-param-specific) when fixing.

#### B49. Lambda assigned to Callable captures reference types BY VALUE silently: aliasing divergence from CPython with no warning

- **Location**: tpyc/parse/nodes.py:564, tpyc/parse/nodes.py:568-571
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: A lambda in Callable context sets captures_by_value=True and codegen emits [p] (copy capture) for a reference-type local. Mutations to the original after lambda creation are invisible to the closure -- CPython closures alias. This is exactly the 'silent copy where CPython would alias' class the project forbids, and unlike field/container stores there is NO 'copies P; use copy()' warning. The by-value capture also masks the nested-def use-after-move bug for lambdas specifically, but only by substituting a silent copy. Additionally TpyLambda.children() returns [], so liveness sees no reads from lambda bodies at all -- any future by-reference lambda capture path would inherit the closure-finding unsoundness with even less protection (captured_names is never consulted by liveness for TpyLambda).
- **Evidence**: Repro /tmp/agents/own/lam2.py:
  p = P()                                # p.vals == [1, 2]
  f: Callable[[], Int32] = lambda: len(p.vals)
  p.vals.append(9)
  print(f())
Generated C++: std::function<int32_t()> f = [p]() ...  (value capture)
Observed output: 2 (CPython prints 3). Zero diagnostics.
- **Fix direction**: Either warn on by-value capture of mutable reference types in Callable context (suggest explicit copy()/factoring), or capture by reference with the same liveness protection nested defs need (after that finding is fixed). At minimum document the divergence; and have liveness consult TpyLambda.captured_names the way it does TpyNestedDef.captured_names.
- **Verifier adjustment**: Behavior reproduced: lam2.py emits 'std::function<int32_t()> f = [p]() ...' (value capture), zero diagnostics, runtime prints 2 where CPython prints 3. But two corrections: (1) by-value capture for Callable context is DOCUMENTED design intent, not an oversight -- docs/LANGUAGE_FEATURES.md:6048-6049: 'Callable captures by value (safe for escaping via std::function)'; the actionable gap is therefore the missing copies-into-owned-storage warning (the design's standard remediation for silent copi...

#### B50. list-literal elements copy reference-type lvalues with no copy warning (sibling .append() warns)

- **Location**: tpyc/sema/list_literals.py:1-69, tpyc/sema/compatibility.py:950-953
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `hunt-own-moves`
- **Problem**: OWNERSHIP_DESIGN.md rule 2 promises a copy warning whenever a pointer-variable is stored into container storage. lst.append(p) correctly warns 'copies P into owned storage'. But the literal form lst = [p, q] stores copies of p and q with zero diagnostics (list-literal element analysis has no copy-warning logic; the warning lives only on the Own[T]-coercion, field-assign, and tuple-literal-in-field paths). Subsequent mutation through lst[0] does not affect p -- silent aliasing divergence from CPython that the design explicitly says must be warned. Also no auto-move at last use for literal elements (always copies).
- **Evidence**: Repro /tmp/agents/own/litmove.py:
  p = P(); q = P()        # p.vals == [1]
  lst = [p, q]
  lst[0].vals.append(9)
  print(len(p.vals))
Generated C++: std::array<P, 2> lst = {p, q};  (plain copies)
Observed output: 1 (CPython prints 2). Zero diagnostics.
Contrast /tmp/agents/own/appendcmp.py (lst.append(p)): 'warning: copies P into owned storage; use copy() to make this explicit'.
- **Fix direction**: Route list/set/dict literal elements through the same copy-warning + last-use-auto-move logic as tuple literals (sema/statements.py:463-580 already implements the pattern for tuples) and .append. Gap-sweep: set and dict literals, and dict comprehension value expressions.

#### B51. for k, v in d.items() yields VALUE COPIES of dict values -- mutation silently lost (CPython aliases)

- **Location**: runtime/cpp/include/tpy/ordered_map.hpp:309-310 (const-only tuple_items iterator), runtime/cpp/include/tpy/dict_ops.hpp:161-178
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: dict_items() exists only for const ordered_map&, and tuple_items_begin() materializes std::tuple<K, V> BY VALUE per element. The for-loop unpack then runs tuple_to_pointer over the value temporary, so the loop variable v points into a copy of the dict's value. 'for k, v in d.items(): v.x = 9' compiles cleanly with no diagnostic and the mutation is discarded -- CPython mutates the dict's object. This is exactly the silent-copy-where-CPython-aliases class for a textbook-idiomatic construct. Sibling inconsistency: 'for v in d.values(): v.x = 9' instead fails the C++ build ('assignment of member ... in read-only object') -- neither matches Python, and the two siblings fail differently.
- **Evidence**: Repro /tmp/agents/s1_items_mut.py: d = {"k": Point(1)}; for k, v in d.items(): v.x = 9; print(d["k"].x). TPy prints 1; CPython (PYTHONPATH=lib/cpy) prints 9. Generated C++: 'auto __tup_1 = ::tpy::tuple_to_pointer<std::tuple<std::string_view, Point*>>(__for_tup_0);' where *__beg_0 is a value std::tuple<std::string, Point> copy.
- **Fix direction**: Add a non-const items view whose iterator yields reference pairs (e.g. std::tuple<const K&, V&> or a proxy) so the borrow-form tuple unpack points into the map; keep the const view for readonly receivers (and then sema must mark v readonly). At minimum, emit the silent-copy warning ('copies Point ...; use copy()') at this boundary. Also reconcile values() to the same semantics.
- **Verifier adjustment**: Bug is real and reproduced, but per calibration this is CPython-parity divergence with a workaround -> medium, not high. Verified: runtime/cpp/include/tpy/ordered_map.hpp:309-310 exposes only const_tuple_items_iterator (tuple_items_begin() const) materializing std::tuple<K,V> by value; generated loop for /tmp/agents/verify/s1_items_mut.py binds 'auto&& __for_tup_0 = *__beg_0' (a value copy) then tuple_to_pointer points v into that copy, so 'v.x = 9' mutates the temporary. TPy prints 1, CPytho...
- *Independently found as*: "dict.items() loop unpack silently loses mutations (runtime iterator yields tuple by value)" (`hunt-silent-copy`)

#### B52. dict.setdefault() and dict.get(k, default) return a copy of the stored value; d.setdefault(k, []).append(x) silently no-ops

- **Location**: runtime/cpp/include/tpy/dict_ops.hpp:123-130 (dict_setdefault), runtime/cpp/include/tpy/dict_ops.hpp:106-111 (dict_get_default)
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `hunt-silent-copy`
- **Problem**: `V dict_setdefault(...)` and `V dict_get_default(...)` return V BY VALUE. CPython returns the stored object (alias). The canonical grouping idiom `d.setdefault(k, []).append(x)` therefore appends to a temporary copy and the mutation vanishes silently; same for `x = d.get(k, default); x.append(...)`. No TPy diagnostic. Inconsistent sibling: one-arg `d.get(k)` (dict_ops.hpp:67-73) correctly returns `V*` into the map and aliases (verified at runtime). Docs claim both methods Working (LANGUAGE_FEATURES.md:651).
- **Evidence**: Repro 1: d: dict[str, list[Int32]] = {}; d.setdefault("a", []).append(1); d.setdefault("a", []).append(2); print(len(d["a"])) -> TPy prints 0, CPython prints 2. Repro 2: d["a"] = [1]; empty: list[Int32] = []; x = d.get("a", empty); x.append(2); print(len(d["a"])) -> TPy prints 1, CPython prints 2. Runtime: `V dict_setdefault(...) { ... return (*m.find(k)).second; }` and `V dict_get_default(...) { ... return (*it).second; }` -- by-value returns. Contrast `V* dict_get(...) { return &((*it).second); }`.
- **Fix direction**: Return a borrow (V& / V*) from both, with sema treating the call result as borrow-form like one-arg get; for get(k, default) the default-absent path needs the default to be insertable or the lifetime handled (e.g. require the default to be an lvalue or split present/absent forms). Note dict.pop variants returning owned V are correct as-is.
- **Verifier adjustment**: Technically confirmed, severity adjusted down. Both repros reproduced: d.setdefault('a',[]).append(1); d.setdefault('a',[]).append(2); len(d['a']) -> TPy 0, CPython 2; and x = e.get('a', empty); x.append(2) -> TPy len 1, CPython 2. No diagnostic. Runtime code verified at dict_ops.hpp:106-111 (dict_get_default returns '(*it).second' by value) and 123-130 (dict_setdefault returns '(*m.find(k)).second' by value), versus one-arg dict_get at lines 67-73 returning 'V*' (&((*it).second)) -- the sibl...

#### B53. walrus binding of a reference type copies into a hoisted std::optional

- **Location**: tpyc/codegen_cpp/expressions.py:5967-6022 (_gen_named_expr pre-declaration)
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `hunt-silent-copy`
- **Problem**: `(q := p)` where p is a class instance pre-declares `std::optional<P> q;` and assigns `q = p` -- a copy. Mutating q afterwards does not affect p, silently diverging from CPython. Same hoisted-optional pattern as the match-capture finding; pointer-repr tuples already get special borrow-form handling in _gen_named_expr, but plain reference types do not. The if/else hoisting path proves the borrow-form hoist (P*) is available.
- **Evidence**: Repro: p = P(); if (q := p).v == 0: q.v = 9; print(p.v) -> TPy prints 0, CPython prints 9. Generated C++: `P p = P(); std::optional<P> q; if (((q = p, *q).v == 0)) { (*q).v = 9; }`
- **Fix direction**: Pre-declare walrus locals of non-value types in borrow form (P*) and assign &(value) when the source is an lvalue borrow, sharing the decision logic with VarDecl/branch-hoist paths; fold into the same fix as match capture since both are 'hoisted maybe-unassigned local' instances.

#### B54. bytearray classified is_value_type=True: local binds, field reads, and returns silently deep-copy where CPython aliases

- **Location**: tpyc/type_def_registry.py:717-728, tpyc/typesys.py:555-563
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `hunt-silent-copy`
- **Problem**: bytearray is registered `is_value_type=True` (with param formatters bolted on for reference-like param passing). Because value types copy silently by definition, every non-param boundary copies the whole buffer with NO warning: `x = ba` emits `std::vector<uint8_t> x = ba;`, `x = obj.data` and `x = get_data(obj)` likewise. Mutations through the copy are lost -- silent CPython divergence -- and each bind is an O(n) buffer copy (perf). CLAUDE.md's own terminology section classifies bytearray as a reference type, and list/dict/set alias correctly at the exact same boundaries (verified), so this is also a sibling inconsistency. Param passing (vector<uint8_t>&) is correct; only the value-type-driven copy paths are wrong.
- **Evidence**: Repro: ba = bytearray(b"ab"); x = ba; x.append(99); print(len(ba)) -> TPy prints 2, CPython prints 3 (no warning). Field/return variants: o.data via `x = o.data` and `x = get_data(o)` both print 2 vs CPython 3. Generated C++: `std::vector<uint8_t> x = ba;`. Registry: `register(TypeDef("builtins.bytearray", TC.BYTES, is_value_type=True, ... param_cpp_formatter=lambda args: "const std::vector<uint8_t>&", param_mut_cpp_formatter=lambda args: "std::vector<uint8_t>&", is_expensive_copy=True, ...))`.
- **Fix direction**: Reclassify bytearray as a non-value (reference) type so locals/field-reads bind borrow form (vector<uint8_t>&/*) and storage-boundary copies go through the existing copy-warning machinery; the param_mut/param formatters then become the normal ref-param path. bytes can stay value-like (immutable, divergence unobservable), but audit any other is_value_type=True + mutable types for the same hole.
- **Verifier adjustment**: Technically confirmed, severity adjusted down. Reproduced the local-bind variant: ba = bytearray(b'ab'); x = ba; x.append(99); print(len(ba)) -> TPy 2, CPython 3, no warning. Registry verified at type_def_registry.py:717-728: is_value_type=True with param_cpp_formatter/param_mut_cpp_formatter bolted on (and a comment conceding 'is_value_type only reflects C++ copy semantics'); typesys.py:555-563 is_ref_param confirms the bolt-on ref-param shim. No doc declares copy-on-bind intended -- LANGUAG...

#### B55. for v in d.values(): v.append(...) fails the C++ build with no TPy diagnostic (values view is const-only)

- **Location**: runtime/cpp/include/tpy/dict_ops.hpp:147-158 (dict_values_view), runtime/cpp/include/tpy/dict_ops.hpp:175-178 (dict_values takes const map)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-silent-copy`
- **Problem**: dict_values_view stores `const ordered_map*` and iterates values_begin() on the const map, so mutating the loop variable hits a raw g++ error ('passing const std::vector<int> as this argument discards qualifiers') with no sema diagnostic. CPython allows mutation through d.values(). Sema accepts the program (no readonly error), so the user gets a C++ wall of text instead of either working code or a clean TPy error. Inconsistent siblings: `d[k].append(...)` works, and d.items() compiles (but silently copies -- see separate finding). Workaround exists (`for k in d: d[k].append(...)`).
- **Evidence**: Repro: d: dict[str, list[Int32]] = {}; d["a"] = [1]; for v in d.values(): v.append(2) -> g++: "error: passing 'const std::vector<int>' as 'this' argument discards qualifiers" at v.push_back(2); no TPy diagnostic. Runtime: `struct dict_values_view { const ordered_map<K, V>* map_; auto begin() const { return map_->values_begin(); } ... }`.
- **Fix direction**: Add a mutable values view (non-const map_ + values iterator over Node::value&) selected when the dict receiver is mutable, mirroring how subscript yields mutable refs; or at minimum have sema reject mutation through values() with a proper diagnostic until the runtime supports it.

#### B56. Ternary with a non-lvalue branch silently copies the reference-type lvalue branch where CPython aliases

- **Location**: tpyc/sema/expressions.py:2514-2575, tpyc/codegen_cpp/expressions.py:6102 (_gen_if_expr, gap-sweep)
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `sema-expr`
- **Problem**: Sema strips Ref/Own from both ternary branches and returns a plain storage-form common type; whether the result aliases is left entirely to codegen. When both branches are stable lvalues codegen binds `std::vector<int32_t>& c = (flag ? a : b)` (aliases, correct). But when one branch is a fresh literal/rvalue, the C++ ternary is a prvalue and the binding copies the lvalue branch: `c = a if flag else [9]` makes `c` a copy of `a`, so a subsequent `c.append(7)` does not mutate `a` -- CPython aliases and both observe the mutation. No diagnostic, output silently diverges. This is exactly the borrow/storage join the audit brief flags: the join point discards the aliasing distinction instead of unifying borrow forms (e.g. materializing the rvalue branch and binding a reference, or diagnosing).
- **Evidence**: Repro /tmp/agents/exprrev/tern_alias2.py: `a = [1, 2]; c = a if flag else [9]; c.append(7); print(a); print(c)` -> TPy binary prints `[1, 2]` then `[1, 2, 7]`; CPython prints `[1, 2, 7]` twice. Control tern_alias.py (both branches locals) emits `std::vector<int32_t>& c = ((flag) ? (a) : (b));` and aliases correctly -- behavior is inconsistent between the two shapes of the same expression.
- **Fix direction**: Either materialize the rvalue branch into a hidden temp and bind the result by reference in both arms (matching the both-lvalue lowering and CPython aliasing), or have sema flag the mixed lvalue/rvalue reference-type ternary (a coerce/ownership fact on TpyIfExpr) so the silent copy is at least a diagnosed copy. The aliasing decision should be a sema-decided AST fact, not codegen shape inspection (THIR note).
- **Verifier adjustment**: Reproduced including runtime divergence. `c = a if flag else [9]` emits `std::vector<int32_t> c = ((flag) ? (a) : (std::vector<int32_t>{9}));` (by-value copy); after `c.append(7)` TPy prints a=[1,2,3] c=[1,2,3,7] while CPython prints [1,2,3,7] twice. Control with both branches lvalues emits `std::vector<int32_t>& c = ((flag) ? (a) : (b));` and aliases correctly -- inconsistent lowering of the same expression shape confirmed. Sema location checks out (_analyze_if_expr at expressions.py:2514+ s...

### Theme: Dangling views / lifetime UB, iterator invalidation  (worst: critical, 5 findings)

#### B57. View-typed local bound to a view over a temporary dangles (StrView/BytesView/Span)

- **Location**: tpyc/sema/local_deduction.py:902-919 (is_view_compatible_source), tpyc/sema/statements.py TpyVarDecl handler (no local lifetime check)
- **Severity / category**: critical / ub -- reproduced: yes -- found by `hunt-dangling-views`
- **Problem**: A local variable whose initializer is a view-returning operation (str .strip()/slice, bytes slice/concat slice, list slice/copy slice) is given the view's C++ type (std::string_view / std::span) with NO check that the view's backing storage outlives the statement. When the source is a temporary (result of str_concat/std::format/bytes_concat/list_copy, or an inline literal), the temporary is destroyed at the end of the initializing full-expression, leaving the local a dangling view. is_view_compatible_source treats 'function/method call returning a view type' as unconditionally view-safe (line 904-908) without recursing into the borrowed-from source; Span/BytesView locals get no lifetime gate at all. Confirmed runtime corruption (use-after-free) on all three families. This is exactly the borrow-form/view-provenance gap the escape-analysis doc claims is handled. Sibling gap: a view local held across a generator yield or async await suspension (frame survives suspension) would dangle identically and is not checked here.
- **Evidence**: str (b2.py): `v = ("   " + x + "...").strip()` -> emitted `std::string_view v = ::tpy::str_strip((::tpy::str_concat(...)));` then `return v + ...`. str_concat returns std::string (format.hpp:198), str_strip returns std::string_view into it (format.hpp:322); temp dies at `;`. RUN `uv run tpy /tmp/agents/views/b2.py` expected 'PAYLOAD this is a fairly long string...' GOT 'M           is a fairly long string...' (leading bytes corrupted).
bytes (n2.py): `bv = (b"ABCDEFGH"+b"IJKLMNOP")[0:8]` -> `std::span<const uint8_t> bv = ::tpy::bytes_slice((::tpy::bytes_concat(...)), ...)`. RUN expected 203 GOT 143.
Span (p_span_copy_slice.py): `sp = src.copy()[1:4]` -> `std::span<int32_t> sp = ::tpy::list_slice(::tpy::list_copy(src), ...)`. RUN expected 90 GOT 486000967.
Contrast SAFE param case (o): `return x.strip()` with x a str param -> `str_strip(x)` borrows caller storage, correct.
- **Fix direction**: Local bindings of view type must run the same lifetime gate as returns: when the RHS is a view-returning call/method, recurse into the source it borrows from (receiver for strip/slice, arg for concat/format) via return_borrows_from and require that source to outlive the local (param/global/field/longer-lived local), not be a temporary. For Span/BytesView add a gate at all. Either reject (error: backing temporary dies) or hoist the temporary into a named local at function scope (as already done for call-arg list literals in cases c/d). Check the same path for views captured across yield/await and into closures.

#### B58. is_dangling_return misses f-strings: returning f"..." (or a slice of one) as StrView dangles

- **Location**: tpyc/sema/compatibility.py:2108-2233 (is_dangling_return)
- **Severity / category**: critical / ub -- reproduced: yes -- found by `hunt-dangling-views`
- **Problem**: is_dangling_return has explicit cases for array/dict/set literals, list-repeat, constructors, binary/unary ops (all 'creates temporary -> True'), but NO case for TpyFString (the class isn't even imported into compatibility.py). f-strings always materialize a fresh std::string via std::format, so a StrView return derived from one dangles. Because TpySubscript recurses into expr.obj, `return f"..."[a:b]` also slips through (obj is the unhandled f-string -> falls to default `return False` = treated safe). Affects all view-return contexts that route through is_dangling_return: function returns, method returns, lambda/yield via check_view_return_dangle.
- **Evidence**: j_direct_fstr_return.py: `def f(n: Int32) -> StrView: return f"val-{n}"` compiles clean; emitted `std::string_view f(int32_t n){ return std::format("val-{}", n); }` -- returns a view into the destroyed std::format temporary.
i_return_slice_temp.py: `return f"prefix-{n}-suffix"[0:6]` (StrView) accepted; emitted `return ::tpy::str_slice(std::format(...), ...)`. g_fstr_local.py runs to garbage (`??_? q`). is_dangling_return default arm at compatibility.py:2232 `return False` reached for TpyFString.
- **Fix direction**: Add `if isinstance(expr, TpyFString): return True` to is_dangling_return (and import TpyFString). More robustly, also handle view-returning method/call-over-temporary in return position symmetrically with finding-1's local fix so `return text.upper()[a:b]` etc. is covered. Verify yield/lambda paths (check_view_return_dangle) inherit the fix.

#### B59. View-alias reassignment overwrites source list -> dangling string_view UB

- **Location**: tpyc/sema/local_deduction.py:981-987, tpyc/sema/statements.py:4121, tpyc/sema/compatibility.py:1415
- **Severity / category**: critical / ub -- reproduced: yes -- found by `macros`
- **Problem**: ViewVarInfo.source_var_ids is meant to record every pending-view source a view local ever aliased, so that if ANY source resolves to owned (str/std::string) the alias is promoted to owned too (local_deduction.py:1019-1034 second pass). At initial binding, statements.py:2686-2694 deliberately collects ALL leaf sources ('collect ALL leaf sources... if either resolves to owned, x must too'). But on reassignment, track_view_reassign_source does `info.source_var_ids = [source_type.var_id]` -- REPLACING the list instead of appending. A view local that first aliased a later-mutated string and was later rebound to a different view source loses the first source, stays std::string_view, and reads of it between the source mutation and the rebinding dereference a reallocated std::string buffer. Silently emitted UB for plain straight-line string code; resolution is flow-insensitive by design so the fix is to append, matching the initial-binding semantics.
- **Evidence**: Repro /tmp/agents/audit/view1.py:
```python
def main():
    s1 = "aaa"
    a = s1
    s1 += "bbb"
    print(a)
    s2 = "zzz"
    a = s2
    print(a)
main()
```
Emitted C++ (uv run tpy --dump-code):
```cpp
std::string s1 = "aaa";
std::string_view a = s1;   // view into std::string
s1 += "bbb";               // may reallocate -> a dangles
std::cout << a << "\n";   // UB read of dangling view
std::string_view s2 = "zzz";
a = s2;
```
Control (delete the last two statements): `a` correctly becomes `std::string a = s1;` -- proving the second-pass promotion works when source_var_ids=[s1] and that the reassignment's overwrite is what defeats it. The sibling subscript path (v = xs[0]; xs.append(...)) is correctly promoted via source_mutated, so the gap is specific to name-alias reassignment.
- **Fix direction**: In track_view_reassign_source (and any other reassign path), append/extend source_var_ids instead of replacing: `info.source_var_ids.append(source_type.var_id)` (dedup optional). Also audit the or/ternary reassignment case (collect_pending_source_types should feed reassignment like it feeds initial binding). Gap-sweep note: check the bytes view family (same ViewTypeFamily machinery) and reassignment inside loops/branches where flow order differs.

#### B60. Inline list-literal slice emits non-compiling C++ (list_slice over brace-init)

- **Location**: tpyc/codegen_cpp (list slice codegen), repro tpyc/sema/local_deduction.py path for Span locals
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-dangling-views`
- **Problem**: Slicing a list literal inline (`[a,b,c][i:j]`) generates `::tpy::list_slice({a,b,c}, BasicSlice{...})`, passing a brace-enclosed initializer list where the template parameter cannot be deduced, so the C++ compiler rejects it. tpyc accepts it and emits code, but the build fails. (Tangential silver lining: this masks the dangling-Span bug for the inline-literal form, but it is still broken codegen for valid Python.)
- **Evidence**: m2.py: `sp = [111,222,...][1:5]` -> `std::span<int32_t> sp = ::tpy::list_slice({111, 222, 333, 444, 555, 666, 777, 888}, ::tpy::BasicSlice{1, 5});` -> g++ error: `no matching function for call to 'list_slice(<brace-enclosed initializer list>, tpy::BasicSlice)'`.
- **Fix direction**: Materialize the list literal into a typed temporary (std::vector<int32_t>) before slicing, as already done for list literals passed as call arguments. Note this temporary must be hoisted to function scope if a Span outlives the statement (ties into finding 1).

#### B61. Loop-body container mutation: UB still emitted behind a non-fatal warning (known family)

- **Location**: tpyc/codegen_cpp/statements.py:4978-5027, BUGS.md:313
- **Severity / category**: low / ub -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: _gen_begin_end_loop caches __beg_N/__end_N before the loop; a structural mutation of the iterated list in the body (remove/append) invalidates both iterators -> UB, where CPython's index-based iteration is well-defined. Sema DOES diagnose (`warning: Mutation of 'xs' while iterating over it`), but it is non-fatal and the binary still exhibits divergent/UB behavior. Reporting as low because BUGS.md:313 already tracks the non-fatal-warning residual for this hazard class; this confirms the plain sync-loop route with an observed divergence.
- **Evidence**: Repro f1: `for x in xs: if x == 2: xs.remove(2); print(x)` -- TPy warns, then prints `1 2 4 4`; CPython prints `1 2 4`. Generated: cached `__beg_0/__end_0` with `::tpy::list_remove(xs, 2)` in the body.
- **Fix direction**: Either promote the warning to an error for provably-invalidating mutations of the directly-iterated local, or lower such loops to index-based iteration. Already-tracked direction in BUGS.md; no new entry needed beyond noting the plain-sync-loop repro.

### Theme: Generators, async, resumable frames  (worst: critical, 4 findings)

#### B62. break/continue out of a CFG-based finally silently skips the finally body (async try/finally-with-await, async with __aexit__, generator yield-in-finally)

- **Location**: tpyc/codegen_cpp/resumable_cfg.py:915-928, tpyc/codegen_cpp/gen_async.py:2936-2974
- **Severity / category**: critical / miscompile -- reproduced: yes -- found by `codegen-orchestration`
- **Problem**: CFGBuilder._build_break/_build_continue emit a plain Fall to the loop's break/continue BB. When that edge crosses a TryRegion whose finally is CFG-based (finally body contains a suspension: user try/finally-with-await, the synthesized async-with finally holding __aexit__, or a generator's yield-in-finally), the finally body is never run: _emit_exit_region_finallies handles only helper-based finallies (TryRegion.finally_helper_name), ExceptRegion.parent_finally, and WithRegion __exit__ -- a TryRegion with captured_exc_field/finally_entry_bb contributes nothing, and the builder does not reroute the break edge through finally_entry_bb (returns are correctly parked via the pending-return slot; break/continue have no analogous path). Cleanup code is silently skipped. Same hole exists for break inside an except handler of such a try (ExceptRegion branch only handles parent_finally, not parent_finally_entry_bb). Not tracked in BUGS.md (BUGS.md:242 is a different return-in-helper-finally entry).
- **Evidence**: Repro 1 (/tmp/agents/async_break_finally.py): `for i in range(3): try: ...; if i==1: break; await asyncio.sleep(0) finally: print('cleanup', i); await asyncio.sleep(0)`. TPy output: body 0 / cleanup 0 / body 1 / after loop. CPython: ... body 1 / cleanup 1 / after loop -- 'cleanup 1' missing. Repro 2 (/tmp/agents/async_with_break.py): break inside `async with CM(i)` skips `exit 1` (CPython prints it). Repro 3 (/tmp/agents/gen_break_finally.py, generator shape): TPy prints 0/cleanup 0/100 and stops; CPython continues cleanup 1/101.
- **Fix direction**: In the builder, route break/continue edges that cross a CFG-based-finally TryRegion through that region's finally_entry_bb (parking the 'pending break target' the way pending returns are parked, e.g. a pending-jump state slot), or teach _emit_exit_region_finallies to reject/handle captured_exc_field regions instead of silently emitting nothing. Gap-sweep: check break inside except-handlers of such tries (ExceptRegion.parent_finally_entry_bb) and nested CFG-finallies (forwarding order), and the generator shape (shares the emitter).

#### B63. Resumable try/except/else builds the else body inside the TryRegion: exceptions raised in else are wrongly caught by the try's own handlers

- **Location**: tpyc/codegen_cpp/resumable_cfg.py:1284-1295
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-orchestration`
- **Problem**: In CFGBuilder._build_try, when the try body falls through, `stmt.else_body` is built via `self._build_block(try_end, stmt.else_body)` while the TryRegion is still pushed (inside the `self._region_stack.append(try_region)` window). The else-body BBs therefore carry the TryRegion in their region_stack and the emitter wraps them in the same C++ catch clauses as the try body. CPython semantics: exceptions raised in the `else` clause are NOT caught by that try's handlers. Only the resumable path is wrong -- the sync emitter handles try/else correctly (verified). Affects async defs and resumable generators (any try containing a suspension).
- **Evidence**: Repro /tmp/agents/async_try_else.py: `try: await sleep(0) except ValueError: print('caught') else: raise ValueError(...)` inside async def. TPy prints: try body / else runs / caught ValueError / after try. CPython prints: try body / else runs / propagated to caller. Sync repro /tmp/agents/sync_try_else.py shows the non-resumable path matches CPython.
- **Fix direction**: Build the else body AFTER popping the TryRegion: pop the region, then `_build_block` the else from try_end (the else BBs then carry the outer region stack), and route the else's normal exit to normal_exit_target (finally must still run after else). Note the else body must still be skipped on the handler path -- the current normal_exit edge structure already gives that. Gap-sweep: while/for-else inside resumable bodies use a different mechanism (exit_bb/after_bb) and look fine, but verify try/else with a CFG-based finally (else exception must still reach finally_entry_bb as an *uncaught* exception).

#### B64. Simple-generator lambda peephole reorders execution: init runs eagerly at construction, post-yield statements run before the value is delivered, and break/continue after yield drop the pending value

- **Location**: tpyc/codegen_cpp/gen_generators.py:122-148 (is_simple_generator), 159-237 (_gen_simple_while_generator, value at 226-231), 240-467 (for variant)
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `codegen-orchestration`
- **Problem**: The peephole compiles `[init]; while cond: yield v; post` into a factory that (a) executes init statements eagerly in the factory body (CPython defers the whole body until first next()), and (b) emits a per-pull lambda of the shape `__val = v; <post stmts>; return __val;` -- post-yield statements execute BEFORE the consumer receives the value, one pull early. Consequences: observable side-effect reordering vs CPython, and when post contains `break`/`continue` (is_simple_generator does not exclude them, only `return`), the computed value is silently dropped (break exits the loop past the `return __val`). The general resumable-frame path gets all of this right, so the same source has different semantics depending on whether the peephole fires -- the exact peephole-vs-frame divergence class. The break shape alone is tracked at BUGS.md:60 [MED small]; the eager-init and side-effect-ordering divergences are not tracked.
- **Evidence**: Repro /tmp/agents/gen_break.py (`while True: yield i; if i>=1: break; i+=1`): TPy prints `0`; CPython prints `0\n1`. Generated lambda: `while (true) { auto __val = ::tpy::BigInt(i); if ((i >= 1)) { break; } i = ...; return std::optional<...>(__val); } return std::nullopt;`. Repro /tmp/agents/gen_order.py: TPy prints `init / created / after 0 / got 0 / after 1 / got 1`; CPython prints `created / init / got 0 / after 0 / got 1 / after 1` -- both init-timing and post-yield ordering diverge.
- **Fix direction**: Either (a) restrict is_simple_generator further: no statements after the yield in the loop body (post_yield must be empty or provably side-effect-free and transfer-free) and no init statements with side effects -- falling back to the resumable frame; or (b) restructure the lambda to deliver-then-resume (a 2-state micro machine: stash post-yield work to run at the next pull, run init on first pull). Given the resumable frame already handles all shapes correctly, shrinking the peephole's eligibility is the low-risk fix.
- **Verifier adjustment**: Technically fully confirmed: is_simple_generator (gen_generators.py:122-148) excludes return (line 133) but not break/continue; _gen_simple_while_generator emits init via _setup_body_scope into the factory body (line 207, eager at construction) and post-yield stmts between '__val = ...' (line 226) and the return (line 230), so post-yield runs before delivery and a break skips the return entirely. Re-ran repros: gen_break.py prints '0' (CPython '0\n1'); gen_order.py prints 'init/created/after...

#### B65. Coro-struct topological sort ignores module qualifiers: cross-module await with a colliding name produces a bogus 'recursive inline await' rejection

- **Location**: tpyc/codegen_cpp/generator.py:614-648 (_inline_await_targets), 743-797 (_emit_resumable_structs index/deps/cycle reject)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-orchestration`
- **Problem**: _inline_await_targets records `(awaited_async_func_name, owner_record_name)` for each inline await but drops the module qualifier (which _make_await_payload in gen_async.py DOES compute via user_module_call/builtin_module_call). _emit_resumable_structs keys its dependency index by `(name, record)` only, so `await helper.bar()` inside local `foo` is treated as a dependency on the LOCAL `bar`. Two local coros that each await same-named functions from another module form a phantom local cycle and the compile is rejected with the 'recursive inline await ... infinite-size' diagnostic, although the real embedded structs are `::tpyapp::helper::__coro_bar` (no cycle). Plausible in real code: mirroring a helper module's API names (wrappers, shims) is idiomatic. Same keying also produces spurious (harmless) ordering edges, and the (name, record) index silently collides for overloaded functions.
- **Evidence**: Repro /tmp/agents/xmod/{helper.py,main.py}: helper defines async foo/bar; main defines async foo (awaits helper.bar) and bar (awaits helper.foo). TPy: `main.py:4: error: recursive inline `await` involving coroutine 'foo' is not supported...`. CPython runs and prints `2 1`.
- **Fix direction**: Carry the module qualifier in _inline_await_targets (mirror _make_await_payload's user_module_call/builtin_module_call check) and skip edges whose target is cross-module (already complete via the included header). Long-term, do what BUGS.md:36 already proposes: source dependency edges from the CFG's sub-future set (single source of truth) instead of a parallel AST walk -- that also fixes the tracked async-with/async-for missing-edge gap.

### Theme: Exceptions and context managers  (worst: medium, 4 findings)

#### B66. with A(), B(): exception in B.__enter__ skips A.__exit__

- **Location**: tpyc/codegen_cpp/statements.py:3196-3236, tpyc/codegen_cpp/statements.py:3256-3279
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: _gen_with emits ALL manager constructions and __enter__() calls in a flat sequence, then nests the try/catch layers around the body only. Python semantics nest the managers themselves (`with a, b:` == `with a: with b:`), so if a later manager's __enter__ raises, earlier managers' __exit__ must run. TPy skips them -- resource leak / missed cleanup on the exception path. Workaround exists (write nested with statements), hence medium.
- **Evidence**: Repro w1: two managers, B.__enter__ raises ValueError. Generated code calls `__ctx_2.__enter__()` outside every try block. Observed runtime: TPy prints `enter A / enter B / caught`; CPython prints `enter A / enter B / exit A / caught`.
- **Fix direction**: Emit each subsequent manager's construction+__enter__ INSIDE the previous manager's try layer (true nesting), i.e. interleave the per-item setup with the layer construction in the reversed-zip loop instead of hoisting all setups first.

#### B67. try/except/else: handler goto jumps over else-body initialization -> C++ compile failure

- **Location**: tpyc/codegen_cpp/statements.py:3950-3966
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: _gen_try_throw emits handler bodies ending in `goto __after_else_N;` and then the else body at the same C++ scope, with the label after it. If the else body declares a variable with non-vacuous initialization that is not hoisted by branch decls (i.e. used only inside the else), the goto crosses the initialization -- ill-formed C++, build fails on valid Python.
- **Evidence**: Repro t1: `try: print("t") except ValueError: print("h") else: ys = [1, 2]; print(len(ys))` -> g++: "jump to label '__after_else_1' ... crosses initialization of 'std::array<int, 2> ys'". Observed compile failure.
- **Fix direction**: Wrap the else body in its own `{ ... }` block (as _gen_match_switch_str already does for trailing arms at match.py:1563-1566 specifically 'to prevent goto from crossing variable declarations'), or place the label before a braced else block. The return-tier else (statements.py:3862-3867) has the same goto-past-else shape and needs the same audit.

#### B68. with-as name reuse across blocks: already-declared path assumes pointer shape and out-of-scope C++ local

- **Location**: tpyc/codegen_cpp/statements.py:3213-3217
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: When the as-target name is already in declared_vars, _gen_with unconditionally emits `name = &(__ctx_N.__enter__());` (pointer shape) unless the name is in optional_locals. Two failure modes: (1) for a value-type enter result this takes the address of an rvalue and assigns it to a non-pointer; (2) declared_vars is not popped when the first with sat inside a for-loop body (a distinct C++ scope), so the second with emits an assignment to a name that is no longer in C++ scope. Valid Python fails to build.
- **Evidence**: Repro w3: first `with Mgr("L") as v:` inside a for loop, then a second `with Mgr("R") as v:` after it -> g++: "'v' was not declared in this scope" and "taking address of rvalue" at `v = &(__ctx_2.__enter__());`. Renaming the second as-var (w4) compiles and runs correctly.
- **Fix direction**: The already_declared branch must dispatch on the var's actual C++ form (value local vs pointer-local vs optional-local) like _gen_var_decl_code's reassignment path does; and with-as names bound inside loop bodies need the same scope-exit cleanup _gen_loop_body does for the loop var (or block-scope snapshot/restore).

#### B69. __init__ control-flow field-assignment check misses try/match/with bodies

- **Location**: tpyc/sema/analyzer.py:2736-2766
- **Severity / category**: medium / unsound-safety -- reproduced: yes -- found by `sema-core`
- **Problem**: _check_init_field_assignments.walk_body recurses into TpyIf, TpyWhile, TpyForEach only. A @nocopy or __del__ field assigned inside a try/except (or match/with) body in __init__ escapes the 'must be initialized unconditionally before any branch' error that the identical if-version triggers. For __del__ fields this is the 'destructor runs on a default-constructed value' hazard the check exists to prevent; for @nocopy it silently diverges from the sibling construct (if errors, try compiles and runs).
- **Evidence**: Repro: @nocopy class R with zero-arg __init__; class CIf assigning self.r = R() inside `if flag:` -> error 'field r ... assigned inside control flow in __init__; R is move-only or has __del__...'. Identical class CTry assigning inside `try:` -> compiles and runs (only the default-construct warning). Verified by build+run: prints 0.
- **Fix direction**: Recurse via stmt.sub_bodies() (the generic accessor already used by validate_multi_base_init_calls.walk_nested) instead of an isinstance whitelist, so try/except/finally, match cases, and with bodies are covered and future statement kinds can't be forgotten.

### Theme: Expression-level miscompiles and rejects  (worst: high, 15 findings)

#### B70. Value-context or/and evaluates RHS unconditionally (short-circuit broken)

- **Location**: tpyc/codegen_cpp/expressions.py:1485-1569 (RHS temp at 1532-1540)
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-expr`
- **Problem**: _gen_logical_value (Python operand-semantics and/or, i.e. non-bool result context like `x = name or fallback()`) materializes any non-TpyName RHS into a hoisted temp statement `auto&& __tmp = <rhs>;` emitted BEFORE the ternary. The RHS is therefore evaluated eagerly even when the LHS short-circuits. CPython evaluates the RHS only when needed; side effects, exceptions/panics (e.g. `x or xs[0]` on empty xs), and arbitrary cost run unconditionally. The bool-result path (line 1819-1835) correctly emits C++ &&/||. Same root cause (TempState hoisting evaluates eagerly) threatens ternary `a if c else b` whenever a branch's codegen creates temps (union-arg temps, adapter temps, ref-param temps in _gen_if_expr branches); the gap-sweep should trace those siblings.
- **Evidence**: Repro /tmp/agents/or_eval.py: `def pick(name: str) -> str: return name or fallback()` where fallback() prints. Generated C++: `std::string pick(std::string_view name) { auto&& __tmp_1 = fallback(); return ((!name.empty()) ? std::string(name) : __tmp_1); }`. Runtime: `pick("alice")` prints "SIDE EFFECT" then "alice"; CPython prints only "alice".
- **Fix direction**: Lower value-context and/or to a GCC statement expression (already used elsewhere) that binds the LHS, tests truthiness, and only evaluates the RHS in the not-taken-shortcut path: `({ auto&& l = lhs; truthy(l) ? l : ({ auto&& r = rhs; r; }); })`, taking care to keep lvalue-ness for non-value types. Audit _gen_if_expr branch generation for the same temp-hoisting eager evaluation.

#### B71. Nested-class constructor kwargs passed positionally in source dict order

- **Location**: tpyc/codegen_cpp/expressions.py:3191-3201
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-expr`
- **Problem**: The is_nested_constructor path in _gen_method_call appends kwargs values after positional args in literal dict order with no mapping to the __init__ parameter order. `Outer.Inner(b=2, a=1)` is emitted as `Outer::Inner(2, 1)`, silently assigning a=2, b=1 -- value corruption with no diagnostic. Plain functions and top-level constructors are normalized by sema (verified: `C(b=2, a=1)` emits `C(BigInt(1), BigInt(2))`), so this is a sibling gap specific to the nested-constructor emission path. The adjacent is_nested_enum_constructor and is_callable_field paths also ignore kwargs entirely -- gap-sweep should check them.
- **Evidence**: Repro /tmp/agents/nested_kw.py: `class Outer: class Inner: def __init__(self, a: int, b: int): ...` then `p = Outer.Inner(b=2, a=1); print(p.a, p.b)`. Generated: `Outer::Inner p = Outer::Inner(2, 1);`. Runtime output `2 1`; CPython prints `1 2`.
- **Fix direction**: Normalize kwargs to positional order against the resolved __init__ FunctionInfo (same normalization the TpyCall constructor path gets from sema), instead of appending raw kwargs values; ideally do it in sema so every constructor-call shape shares it.

#### B72. Tuple-literal rvalue-into-borrow elements generated twice; orphaned temps double-evaluate side effects

- **Location**: tpyc/codegen_cpp/expressions.py:5302-5310 (first pass), 5331-5353 (rv_borrow regen at 5349)
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-expr`
- **Problem**: _gen_tuple_literal always renders each element once into elem_str (line 5303-5310), then for rvalue-into-borrow-slot elements re-generates the element via gen_expr_deref (line 5349) for the tuple_value_to_borrow helper path, discarding elem_str. Any temps registered during the discarded first generation (e.g. _gen_union_arg's variant temp wrapping a call argument, dynamic-protocol adapter temps, Own copy-temps) are still flushed as statements, so their side-effecting initializers execute at runtime in addition to the used second generation -- the argument expression runs twice. This is exactly the duplicate-TempState hazard the code itself warns about for call paths (comment at 3224-3230) but the tuple-literal second pass is unguarded.
- **Evidence**: Repro /tmp/agents/tuple_dbl.py: `use((h(g()), 7))` where h(u: int | str) -> Own[A] and g() prints. Generated main: `std::variant<::tpy::BigInt, std::string> __tmp_1 = g(); std::variant<...> __tmp_2 = g(); std::cout << use(::tpy::tuple_value_to_borrow<...>(std::tuple<A, ::tpy::BigInt>{h(__tmp_2), ::tpy::BigInt(7)})) ...` -- __tmp_1 is orphaned. Runtime prints "g called" twice; CPython once.
- **Fix direction**: Decide the rv_borrow classification BEFORE rendering (it depends only on slot mode, is_rvalue_source, and elem_target -- all available pre-render) and generate each element exactly once; or snapshot/rollback the TempState around the speculative first render.

#### B73. String match/case switch dispatch miscompiles for non-ASCII literals (codepoint vs byte mismatch)

- **Location**: tpyc/codegen_cpp/string_dispatch.py:58-70, tpyc/codegen_cpp/match.py:1500,1531-1545
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-expr`
- **Problem**: find_best_discriminator buckets case strings by Python len(s) (codepoints) and ord(s[pos]) (codepoint at codepoint index), but the emitted C++ switches on __match_subject.size() (UTF-8 bytes) and static_cast<unsigned char>(__match_subject[pos]) (byte at byte index). With >= STRING_SWITCH_THRESHOLD (5) unguarded string cases, any non-ASCII literal lands in the wrong bucket: its case is unreachable and the subject falls through to the default/wildcard arm. char_at mode is also wrong when the discriminating character is non-ASCII (case '<codepoint>' vs UTF-8 lead byte) or when any string has multibyte chars before the position. generator.py:2145 uses the same helper for enum-member-name dispatch; Python identifiers may be non-ASCII too.
- **Evidence**: Repro /tmp/agents/match_len.py: match with cases "x","xy","xyz","wxyz","café" (forces length discriminator; len("café")==4 but UTF-8 size()==5). Generated: `case 4: { if (__match_subject == "wxyz") ... if (__match_subject == "café") ... }` with no case 5. Runtime: classify("café") prints 0 (wildcard arm); CPython prints 5.
- **Fix direction**: Compute buckets over the UTF-8 byte encoding: use len(s.encode('utf-8')) for length and the byte value s.encode('utf-8')[pos] for char_at (restricting positions to min byte length), so compile-time bucketing matches the runtime byte-wise switch. Alternatively skip switch dispatch when any case string is non-ASCII.

#### B74. cpp_template placeholders repeated in template double-evaluate receiver/args (UB potential with std::stable_sort)

- **Location**: tpyc/codegen_cpp/context.py:85-108 (expand_cpp_template), tpyc/codegen_cpp/builtins.py:84-108 (gen_call_from_fi), lib/tpy/tpy/_builtins/_list.py:108, lib/tpy/tpy/_core/_containers.py:62, lib/tpy/tpy/unsafe.py:194
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-expr`
- **Problem**: expand_cpp_template substitutes the generated receiver/arg strings textually into the template with no purity check, so templates that repeat a placeholder evaluate the expression multiple times. list.sort() is `std::stable_sort({self}.begin(), {self}.end())`: a subscript receiver with a side-effecting index is evaluated twice, and if the index returns different values on the two calls, begin() and end() come from DIFFERENT containers -- std::stable_sort over iterators from different ranges is undefined behavior. unsafe.py's `std::vector<uint8_t>({0}, {0} + {1})` repeats arg 0 similarly. User @cpp_template code has the same unguarded hazard.
- **Evidence**: Repro /tmp/agents/sort_rv.py: `rows[key_fn()].sort()` where key_fn() prints. Generated: `std::stable_sort(::tpy::__getitem__(rows, key_fn().to_fixed_check<int32_t>()).begin(), ::tpy::__getitem__(rows, key_fn()...).end());` (observed two key_fn calls). Runtime prints "key computed" twice; CPython once. A key_fn returning 0 then 1 would sort [rows[0].begin(), rows[1].end()) -- UB.
- **Fix direction**: In gen_call_from_fi / expand_cpp_template, when a placeholder occurs more than once and its expression is not a simple lvalue (name/this/field chain), bind it to an `auto&&` temp first and substitute the temp name. Also note expand_cpp_template's sequential str.replace can corrupt output if an earlier-substituted arg's code contains a literal `{N}` (e.g. a string literal "{1}") -- use a single-pass regex substitution.

#### B75. while-condition temps flushed once before the loop; mutated temp reused across iterations

- **Location**: tpyc/codegen_cpp/statements.py:4850-4852
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: _gen_while generates the condition, then flushes pending temps at the loop header indent BEFORE `while (cond)`. Any temp the condition creates (e.g. a container literal materialized to bind a mutable-reference parameter, TempState.create) is initialized exactly once; every iteration re-reads the same temp object, including mutations the callee made. CPython evaluates the full condition expression -- including fresh literals -- every iteration.
- **Evidence**: Repro wh1: `def take(xs: list[Int32]) -> Int32: xs.append(7); return len(xs)` and `while take([1]) == 2 and n < 3: n += 1`. Generated: `std::vector<int32_t> __tmp_1 = {1}; while (((take(__tmp_1) == 2) && (n < 3))) {...}`. Observed: TPy prints 1, CPython prints 3.
- **Fix direction**: Re-evaluate per iteration: lower a temp-carrying while condition to `for (;;) { <temps>; if (!(cond)) break; <body> }` (or reset the temps at the top of each iteration). _gen_if has the same flush-before-header shape but if-conditions evaluate once, so only loops are affected; check do-while-like constructs and the assert path for siblings.

#### B76. Walrus in chained comparison: short-circuit skips assignment but sema marks it definitely-assigned (uninitialized read, UB)

- **Location**: tpyc/sema/expressions.py:1411-1421, tpyc/sema/expressions.py:777-795
- **Severity / category**: high / ub -- reproduced: yes -- found by `sema-expr`
- **Problem**: _analyze_chained_compare desugars `a < b < (t := f())` into pairs and analyzes each pair sequentially with no save/rollback of definitely_assigned, unlike the `&&`/`||` path (lines 777-795) which explicitly rolls back walrus vars introduced in a short-circuited RHS and records them in sc_and_walrus. A walrus in any comparator after the first is therefore treated as unconditionally assigned, while codegen emits a short-circuiting `&&` chain that skips the assignment at runtime. Reading the variable afterwards reads an uninitialized C++ local (UB). The equivalent explicit `and` form is correctly rejected with "variable 't' may not be assigned at this point".
- **Evidence**: Repro /tmp/agents/exprrev/chain_walrus2.py:
  a = 10; b = 1
  if a < b < (t := f()):  # a<b is False -> RHS skipped
      pass
  print(t)
CPython: NameError. TPy: compiles cleanly and printed garbage `32765` (uninitialized int32_t read). Control /tmp/agents/exprrev/and_walrus.py with `if a < b and b < (t := f())` is rejected: "and_walrus.py:9: error: variable 't' may not be assigned at this point".
- **Fix direction**: In _analyze_chained_compare, treat comparators after the first like the `&&` RHS: snapshot definitely_assigned before analyzing each pair beyond pair 0, record walrus vars into sc_and_walrus, and roll back -- ideally by reusing the same save/rollback block _analyze_binop uses for `&&`. Sibling check for gap-sweep: narrowing facts from earlier pairs are also not propagated/limited across pairs (the same conditional-evaluation modeling gap).
- **Verifier adjustment**: Reproduced independently. tpyc/sema/expressions.py:1411-1421 (_analyze_chained_compare) analyzes each desugared pair with no definitely_assigned snapshot/rollback, while the &&/|| path at lines 777-795 explicitly rolls back RHS walrus vars into sc_and_walrus/sc_or_walrus. Repro `if a < b < (t := f()): ...; print(t)` (a=10,b=1) compiles cleanly and emits `int32_t t; if (((a < b) && (b < (t = f())))) {} std::cout << t` -- the && short-circuits past the assignment and `t` is read uninitialized (...

#### B77. `x or default` / `and`-`or` with mixed operand types silently typed bool and emitted as raw C++ ||

- **Location**: tpyc/sema/expressions.py:729-769, tpyc/sema/expressions.py:1185-1197
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `sema-expr`
- **Problem**: _logical_op_result_type returns BOOL whenever the two operand types differ (and neither is an int literal matching an integer other side). Python's `x or y` returns an operand. The fallout for the extremely common default idiom `v = x or 0` on `x: Int32 | None`: the expression is typed bool and codegen emits `(x || 0)` -- std::optional's operator bool -- so `v` is `True` instead of `5` (silently wrong output, reproduced at runtime). Variants: `sv or "default"` (StrView vs str literal) emits `bool s = (sv || "default")` which is invalid C++ (string_view has no operator bool); and when flow narrowing has already narrowed the Optional (e.g. `x: Int32 | None = 5; v = x or 0`), the result is typed Int32 but codegen renders the optional-storage variable with a functional cast `int32_t(x)` -- invalid C++ ("invalid cast from std::optional<int> to int32_t", reproduced). Two different record types `a and b` similarly type as bool and emit raw `&&` (no truthiness conversion). No sema diagnostic in any of these cases.
- **Evidence**: Repro 1 (/tmp/agents/exprrev/or_opt2.py): `x = get(True)` (Optional[Int32]); `v = x or 0; print(v)` -> generated `bool v = (x || 0);` -> binary prints `True`; CPython prints `5`. Repro 2 (or_str.py): `sv = "abc"[0:2]; s = sv or "default"` -> `bool s = (sv || "default");` (invalid C++). Repro 3 (or_mismatch.py): `x: Int32 | None = 5; v = x or 0` -> `int32_t v = (::tpy::is_truthy(x) ? int32_t(x) : __tmp_1);` -> g++: "error: invalid cast from type 'std::optional<int>' to type 'int32_t'".
- **Fix direction**: Mixed-type and/or should either (a) compute a proper common type like the ternary path does (Optional[T] or T -> T with unwrap; None-able unions -> inner; widen numerics; error with a clean diagnostic otherwise), reusing _ternary_common_type + coerce_expr since `x or y` is exactly `x if x else y`; or (b) at minimum raise a sema error instead of typing bool. The narrowed-Optional rendering bug also shows codegen's _gen_logical_value re-derives operand C++ types from sema types without the optional-storage deref that narrowed names need -- a borrow/storage-form fact that should be on the AST node (THIR note).

#### B78. print() with runtime sep=/end= expression re-evaluates it per separator gap

- **Location**: tpyc/codegen_cpp/builtins.py:294-302 (chain_token), 327-330
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `codegen-expr`
- **Problem**: gen_print renders a non-literal sep= (and end=) expression once via _gen_expr_deref and then splices the same C++ string between every pair of arguments. With N args the sep expression is emitted N-1 times, so a side-effecting or expensive sep is evaluated N-1 times; CPython evaluates each call argument exactly once. Also a minor ordering divergence: file= is evaluated first (as the << chain head) and sep interleaves between args, vs CPython's strict left-to-right argument evaluation before the call.
- **Evidence**: Repro /tmp/agents/sep_eval.py: `print(1, 2, 3, sep=get_sep())` where get_sep() prints. Generated: `std::cout << 1 << get_sep() << 2 << get_sep() << 3 << "\n";` -- two calls (N-1) instead of one.
- **Fix direction**: Bind non-literal sep/end (and file) expressions to hoisted temps once (ctx.temps.create) and splice the temp name into the chain.

#### B79. Pointer-repr Optional truthiness ignores inner emptiness (if xs: on Optional[list] treats [] as truthy)

- **Location**: tpyc/codegen_cpp/expressions.py:1396-1436 (_truthy_for_rendered fall-through at 1435-1436 vs value-Optional handling at 1415-1416)
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `codegen-expr`
- **Problem**: Value-repr Optionals get `::tpy::is_truthy(opt)` which implements Python semantics (engaged AND inner truthy; runtime/cpp/include/tpy/core.hpp:342-358). Pointer-repr Optionals (Optional[list]/dict/set/str-param/class locals lowered to T*) fall through to the bare C++ pointer conversion -- only a null check. `if xs:` with xs == [] enters the branch; CPython does not. Classic borrow-form/storage-form sibling gap: the storage form has the parity handling, the borrow form lacks it. Workaround exists (`if xs is not None and len(xs):`), hence medium per project calibration.
- **Evidence**: Repro /tmp/agents/opt_truthy.py: `def check(xs: list[int] | None) -> int: if xs: return 1; return 0`. Generated: `if (xs) { ... }` (xs is `const std::vector<BigInt>*`). Runtime: check([]) prints 1; CPython prints 0.
- **Fix direction**: In _truthy_for_rendered / gen_truthy_expr, when the declared type is pointer-repr OptionalType, emit `(ptr && truthy(*ptr))` using the inner type's truthiness rule (reusing the existing per-type dispatch), mirroring tpy::is_truthy.

#### B80. 'g is not None' on a module-global value-optional emits ill-formed C++ (pointer comparison on std::optional)

- **Location**: tpyc/codegen_cpp/expressions.py:1720-1732 (is_indirect_name branch of is-None emission)
- **Severity / category**: medium / crash -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: The is/is-not-None emitter routes 'indirect names' to pointer comparison ('!= nullptr') unless needs_optional_to_ptr_lift. A module-level global of value-optional type (Int32 | None) is classified indirect but is emitted as a plain 'std::optional<int32_t> g;' storage global, so the condition becomes '(g != nullptr)' -- no such operator; the C++ build fails on valid Python. The identical local works ('g.has_value()'). Global-vs-local sibling divergence in a core narrowing construct.
- **Evidence**: Repro /tmp/agents/n1b_global_val.py: g: Int32 | None = None (module global); def main(): global g; g = 5; if g is not None: ... Emitted 'if ((g != nullptr))' over 'std::optional<int32_t> g;'. Build: g++ 'no match for operator!= (operand types are std::optional<int> and std::nullptr_t)'. Local version (/tmp/agents/n1c_local_val.py) correctly emits g.has_value().
- **Fix direction**: In the indirect-name branch, value-optional globals stored as std::optional must take the has_value() form -- key the choice on the global's actual storage form (pointer slot vs value storage), not on is_indirect_name alone.

#### B81. User-defined __truediv__/__floordiv__: codegen forces div result to Float and both dunders emit the same C++ operator/

- **Location**: tpyc/sema/operators.py:31-71 (DUNDER_CPP_TEMPLATES), tpyc/codegen_cpp/types.py:121-122
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `sema-expr`
- **Problem**: Two related defects in user-type division. (1) Sema resolves `a / b` to the user's __truediv__ and types the expression with its declared return type, but codegen's get_resolved_type has an unconditional "True division always returns float" rule (types.py:121-122) that overrides the resolved binop in contexts without a target type -- `print(a / b)` wraps the std::string result in `::tpy::print_float(...)` -> C++ error (reproduced); a numeric non-float return would silently print as float. (2) DUNDER_CPP_TEMPLATES maps __floordiv__ to `({self}) / ({0})` (operators.py:37) and record codegen emits a `friend operator/` for BOTH __truediv__ and __floordiv__, so a class defining both gets two identical `friend std::string operator/(const Num&, const Num&)` definitions (redefinition error, observed in dump), and even with unique emission `a // b` would dispatch to operator/ i.e. potentially the truediv implementation.
- **Evidence**: Repro /tmp/agents/exprrev/truediv_print.py: class Num with `__truediv__(self, other) -> str`; `print(a / b)` -> generated `std::cout << ::tpy::print_float(((a) / (b)))` -> g++: "no matching function for call to 'tpy::print_float::print_float(std::string)'". (Assignment form `s = a / b; print(s)` works, isolating the codegen type re-derivation.) Repro floordiv.py dump shows two identical `friend std::string operator/(const Num& lhs, const Num& other)` definitions, one calling __truediv__ and one calling __floordiv__.
- **Fix direction**: Codegen must prefer expr.resolved_binop's return type over the div-always-float heuristic (the heuristic should only apply to builtin numeric operands). For dispatch, __floordiv__'s template should call the named method (`({self}).__floordiv__({0})`) rather than C++ `/`, and record codegen should not emit operator/ for __floordiv__. Classic consumer-side re-derivation the THIR migration notes warn about.

#### B82. ==/ordering between incompatible builtin types returns BOOL with no resolution check -> C++ error wall (and `1 == "x"` is valid Python)

- **Location**: tpyc/sema/expressions.py:1028-1061, tpyc/sema/expressions.py:1506-1538 (_validate_comparison)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-expr`
- **Problem**: For comparison operators, _validate_comparison only fires when at least one operand is a *user record*; for builtin operand pairs sema calls operators.resolve_binop and, when it returns None (no matching __eq__/__lt__ overload), silently returns BOOL with expr.resolved_binop unset. Codegen then emits the raw C++ operator: `1 == "x"` becomes `(n == s)` with int32_t vs std::string_view -> a multi-hundred-line overload-resolution error from g++ instead of a sema diagnostic. Note `1 == "x"` is *valid* Python (False), so for ==/!= this is also a parity gap (constant-folding to False or a clean error are both defensible; a g++ wall is not). Same hole for `<`/`>` etc. where CPython raises TypeError -- TPy statically rejecting would be fine, but it should be a sema error. Sibling inconsistency: user records get a precise "no '__eq__' method defined" error.
- **Evidence**: Repro /tmp/agents/exprrev/eq_mismatch.py: `n = 1; s = "x"; print(n == s)` -> generated `(n == s)` -> g++ error wall ("no known conversion for argument 1 from 'int32_t' ... to 'std::thread::id'" among dozens of candidates).
- **Fix direction**: After resolve_binop fails for a comparison, verify the operand pair is actually comparable (shared numeric family, both str-family, same enum, etc.) and raise a sema error otherwise -- mirroring the user-record _validate_comparison message. For ==/!= specifically, decide a language rule: either statically reject cross-family equality (document the Python divergence) or fold to False/True.

#### B83. Chained comparison re-analyzes the shared middle node: spurious 'walrus reassignment' error and non-idempotent analysis hazards

- **Location**: tpyc/sema/expressions.py:1411-1421, tpyc/sema/expressions.py:2439-2454, tpyc/sema/expressions.py:883-895, tpyc/sema/expressions.py:2056-2079
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-expr`
- **Problem**: _analyze_chained_compare builds TpyBinOp pairs where each middle comparator node appears in two pairs and is analyzed twice. analyze_expr is not idempotent: (1) a walrus middle binds on the first pass and hits the 'reassignment' branch on the second, producing a bogus "walrus reassignment of non-value local" error for a single binding (reproduced); (2) a non-empty container-literal middle mints a second literal_id + ListLiteralInfo into pending_resolutions (the empty-literal path grew an explicit cache at 2063 for exactly this re-analysis problem; non-empty literals have none); (3) the enum/bool `is` lowering mutates expr.op in place (883-895), so re-analysis sees a different operator. Each is a separate consequence of the same double-analysis design.
- **Evidence**: Repro /tmp/agents/exprrev/chain_walrus.py: `ys = [2]; if [1] < (zs := ys) < [3]:` -> "chain_walrus.py:3: error: walrus reassignment of non-value local 'zs' is not supported yet; use a separate assignment statement" -- there is no reassignment; the single walrus is analyzed twice (once as pair0.right, once as pair1.left). Code: `for op, comp in zip(expr.ops, expr.comparators): pair = TpyBinOp(prev, op, comp, ...); self.analyze_expr(pair); ...; prev = comp`.
- **Fix direction**: Analyze each comparator exactly once (analyze leaf expressions first, then resolve each pair using cached expr types), or short-circuit re-analysis via ctx.get_expr_type at analyze_expr entry for already-typed shared nodes. This also dovetails with the short-circuit/definite-assignment fix (separate critical finding). THIR note: the in-place expr.op rewrite for enum-`is` should become a node fact rather than AST mutation.
- **Verifier adjustment**: Core claim confirmed by repro: /tmp/agents/verif/chain_walrus.py (`if [1] < (zs := ys) < [3]:` with single walrus binding) -> 'walrus reassignment of non-value local 'zs' is not supported yet'. Mechanism verified: _analyze_chained_compare (expressions.py:1411-1421) analyzes each shared comparator twice (as pair.right then next pair.left); the walrus path's existing-binding lookup (:2439-2454) fires on the second pass. Sub-claim (2) verified at code level: empty-literal path has an explicit re...

#### B84. F-string format spec rejected on int literal but accepted on Int32 variable

- **Location**: tpyc/sema/expressions.py:3605-3609
- **Severity / category**: low / rejects-valid -- reproduced: yes -- found by `sema-expr`
- **Problem**: _analyze_fstring rejects format specs when the part's type is BigInt OR IntLiteralType: `f"{42:>5}"` errors with "Format specs on int are not yet supported (use a fixed-width type like Int32)" even though the literal 42 would resolve to Int32 (the suggested type!) in any other context. Binding it first (`n: Int32 = 42; f"{n:>5}"`) works. The IntLiteralType case should resolve via default_int_for_literal before the BigInt gate, consistent with how literals adopt concrete types everywhere else.
- **Evidence**: Repro /tmp/agents/exprrev/fstr.py: `print(f"{42:>5}")` -> "fstr.py:2: error: Format specs on int are not yet supported (use a fixed-width type like Int32)". Control fspec_var.py: `n: Int32 = 42; print(f"{n:>5}")` -> compiles, prints '   42'.
- **Fix direction**: In the format-spec gate, resolve IntLiteralType via ctx.default_int_for_literal first and only reject when the resolved type is BigInt (matching the configured default-int policy).

### Theme: Pending container-literal resolution  (worst: medium, 10 findings)

#### B85. ICE: dict literal with list-literal value crashes codegen (PendingListType unresolved)

- **Location**: tpyc/typesys.py:3662 (raise site); root cause in sema PendingListType resolution (outside assigned files)
- **Severity / category**: medium / crash -- reproduced: yes -- found by `codegen-expr`
- **Problem**: `d = {1: [3, 1, 2]}` -- a dict literal whose value is a list literal -- aborts compilation with `Internal error: PendingListType should be resolved before codegen (literal_id=0)`. The annotated form `d: dict[int, list[int]] = {1: [3, 1, 2]}` is instead rejected with a bogus diagnostic `Type mismatch in variable 'd': expected list[int], got PendingList[int, 3]#0`. The PendingListType (fixed-size list-literal candidate) resolution pass apparently never visits list literals nested as dict-literal values. Found while reproducing an expression-emission issue; not in my assigned files but mainstream code. Existing tests only cover dict-of-list via empty-dict + setitem (tests/cases/dict/subscript_collection_literal), so the literal form is untested.
- **Evidence**: Repro /tmp/agents/dictlist.py: `def main(): d = {1: [3, 1, 2]}; print(d[1])` -> exit 1, `Internal error: PendingListType should be resolved before codegen (literal_id=0)`. Annotated repro /tmp/agents/sort_recv2.py -> `error: Type mismatch in variable 'd': expected list[int], got PendingList[int, 3]#0`.
- **Fix direction**: Extend the PendingListType resolution walk (sema) to list literals in dict-literal value (and key) positions, and to set-literal elements; the dict value slot's declared element type should resolve the pending literal the same way a list-literal element slot does.
- **Verifier adjustment**: Facts confirmed exactly as described: /tmp/agents/dictlist.py (`d = {1: [3, 1, 2]}`) exits 1 with `Internal error: PendingListType should be resolved before codegen (literal_id=0)` (raise site tpyc/typesys.py:3662), and the annotated form rejects with the bogus `Type mismatch in variable 'd': expected list[int], got PendingList[int, 3]#0`. Severity adjusted from high to medium: this is a loud compile-time failure, never wrong runtime output, and I verified a trivial workaround compiles cleanl...

#### B86. Mixed-length nested list literals rejected; diagnostic leaks internal PendingList type names

- **Location**: sema list-literal unification (outside assigned files; surfaced via tpyc/typesys.py PendingListType)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-expr`
- **Problem**: `xs = [[3, 1, 2], [5, 4]]` -- a plain nested list with rows of different lengths -- is rejected: the inner literals stay distinct PendingList candidates that never unify to list[list[int]]. The suggested annotation in the error message is internal compiler syntax (`list[PendingList[Int32, 3]#0 | PendingList[Int32, 2]#1]`) that a user cannot write. Equal-length rows compile (verified), so the rejection is purely a unification gap, not a design restriction.
- **Evidence**: Repro /tmp/agents/sort_recv3.py: `xs = [[3, 1, 2], [5, 4]]` -> `error: List literal has mixed types: element 2 is PendingList[Int32, 2]#1, but earlier elements are PendingList[Int32, 3]#0. Use a type annotation like list[PendingList[Int32, 3]#0 | PendingList[Int32, 2]#1]`. Control: `[[3, 1, 2], [6, 5, 4]]` compiles and runs.
- **Fix direction**: When unifying list-literal element types, widen differing-size PendingList candidates of the same element type to list[T] before reporting a mismatch; at minimum, render PendingList as list[T] in diagnostics.

#### B87. List-literal locals passed to mutating methods/ctors/__call__ stay std::array (only free functions feed list-vs-Array resolution)

- **Location**: tpyc/codegen_cpp/records.py:406-414 (ctor signature side is correct; root cause in sema container resolution / mutation propagation, outside assigned files)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-funcs-records`
- **Problem**: A local `xs = [1]` is lowered to `std::array<int, 1>` (the not-mutated peephole) even when it is passed to a constructor, a regular method, or a callable object's __call__ that appends to it. The callee side is emitted correctly (ctor/method param becomes non-const `std::vector<BigInt>&` thanks to mutated_params), so the call site fails with a raw C++ conversion error. The identical shape through a FREE function works (xs becomes vector). This is a function-vs-method/constructor sibling asymmetry in whatever feeds the list-vs-Array decision; observed through the ctor/method emission paths in records.py/functions.py but the fix belongs in sema -- flagging for the gap-sweep round.
- **Evidence**: ```python
class Holder:
    def fill(self, xs: list[int]) -> None: xs.append(self.v)
...
xs = [1]
h.fill(xs)
```
C++: `error: cannot convert 'std::array<int, 1>' to 'std::vector<tpy::BigInt>&'` (callee emitted as `void Holder::fill(std::vector<::tpy::BigInt>& xs) const`). Same failure for `Sink(xs)` ctor and `f(xs)` via __call__. Free-function variant `add_one(xs)` compiles and prints correctly.
- **Fix direction**: Make the container-kind resolution consult method/constructor/__call__ mutated_params the same way it consults free-function calls. Sibling constructs to check: generator/async methods, @overload impls, protocol-typed receivers.
- **Verifier adjustment**: Bug is real and reproduced for both method and constructor, but severity recalibrates from high to medium: the array-vs-vector& mismatch always fails loudly at C++ compile time (cannot bind std::array to std::vector&, const or not), never silently copies or mis-runs, and the workaround is trivial (annotate 'xs: list[int] = [1]' or mutate locally first). Method repro: caller emits 'std::array<int32_t, 1> xs = {1};' while callee is 'void Holder::fill(std::vector<::tpy::BigInt>& xs)'; build fail...

#### B88. _emit_branch_decls crashes on PendingListType (list literal first declared in return-tier try body)

- **Location**: tpyc/codegen_cpp/statements.py:4718-4765, tpyc/typesys.py:3662
- **Severity / category**: medium / crash -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: _emit_branch_decls passes the if_branch_decls-recorded type straight to types.type_to_cpp without resolving pending container types (unlike _resolve_target_type which calls _resolve_pending_container, statements.py:1047). When sema records an unresolved PendingListType for a var hoisted out of a return-tier try body, codegen aborts with an internal error. A `ys = [1, 2, 3]` as the first statement of a try over @error_return calls is entirely ordinary code.
- **Evidence**: Repro t2d: `try: ys = [1, 2, 3]; parse(""); print(len(ys)) except ParseError: ...` with @error_return parse -> `Internal error: PendingListType should be resolved before codegen (literal_id=0)`. Traceback: gen_stmt -> _emit_branch_decls (statements.py:4765) -> type_to_cpp -> typesys.py:3662. Same program with a record instead of a list compiles (t2e), and the same list in a throw-tier try compiles (t4).
- **Fix direction**: Resolve pending containers in _emit_branch_decls (call _resolve_pending_container / _normalize_decl_type_for_cpp on raw_var_type) -- and investigate why sema leaves the pending type unresolved only for the return-tier try shape (possible sema-side fix too). Gap sweep: check PendingDict/PendingSet and PendingViewType through the same path.

#### B89. List swap `xs, ys = ys, xs` fails two ways: bogus pending-type mismatch, or RecursionError

- **Location**: tpyc/sema/context.py:1220-1222, tpyc/sema (tuple-unpack compatibility check)
- **Severity / category**: medium / crash -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: Sibling-area finding discovered via the tuple-unpack focus (sema-side, not in my assigned files -- flagging for the gap sweep). Unannotated: `xs = [1]; ys = [2]; xs, ys = ys, xs` is rejected with 'Type mismatch in tuple unpacking: expected PendingList[IntLiteral(1), 1]#0, got PendingList[IntLiteral(2), 1]#1' -- two structurally-identical list types compared by pending literal id, and internal Pending type names leak into a user diagnostic. Annotated (`xs: list[Int32]`): the compiler crashes with RecursionError -- mark_param_mutated (sema/context.py:1222) recurses forever on the xs<->ys alias cycle the swap creates, triggered by the later `xs.append(99)`.
- **Evidence**: Repro s1: error text above. Repro s3 with annotations: `Internal error: maximum recursion depth exceeded`; traceback tail: `sema/context.py:1222 in mark_param_mutated` repeated (alias cycle).
- **Fix direction**: Tuple-unpack compatibility must resolve pending containers before comparing (and never print Pending names). mark_param_mutated needs a visited-set to break alias cycles. Codegen would then also need verification that the swap aliases correctly (pointer-local rebind ordering) -- untested because sema rejects/crashes first.

#### B90. List literal local inferred as Array[T,N] then appended into list[list[T]] emits invalid C++ (push_back(std::array) into vector<vector>)

- **Location**: tpyc/codegen_cpp/expressions.py (list-literal Array inference / append path); repro-confirmed end-to-end
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-perf`
- **Problem**: Plausible, idiomatic Python -- build a list of lists in a loop -- produces C++ that does not compile: `inner = [i, i + 1]` is inferred/emitted as `std::array<int32_t, 2>` (fixed-size literal optimization for locals), but `outer.append(inner)` with `outer: list[list[Int32]]` emits `outer.push_back(std::move(inner))` with no array->vector conversion. Sema accepts the program (overload/compat treats the literal as list-compatible) and the failure surfaces only as a raw g++ template error pointing into generated code, which the user cannot map back to their Python. The boundary where the Array-typed local flows into a list[T] sink is missing a coercion; the value-vs-storage decision made at the literal's binding is not re-checked at the consumer.
- **Evidence**: Repro /tmp/agents/perf/probe1.py: `def nested() -> Own[list[list[Int32]]]: outer: list[list[Int32]] = []; for i in range(3): inner = [i, i + 1]; outer.append(inner); return outer`. --dump-code emits `std::array<int32_t, 2> inner = {i, (::tpy::add_check<int32_t>(i, 1))}; outer.push_back(std::move(inner));` inside `std::vector<std::vector<int32_t>> nested()`. `uv run tpy probe1.py` fails: "error: no matching function for call to 'std::vector<std::vector<int>>::push_back(std::remove_reference<std::array<int,2>&>::type)'" (g++-14). Workaround: annotate `inner: list[Int32] = [i, i + 1]`.
- **Fix direction**: Either (a) when sema finalizes a pending list literal's type, consider all sinks the local flows into and keep it `list[T]` if any sink requires list storage, or (b) emit an explicit conversion at the sink (`std::vector<int32_t>(inner.begin(), inner.end())` / move-construct). Gap-sweep should check the sibling boundaries of the same inference: Array-inferred locals passed as `list[T]` function args, returned as `list[T]`, stored into `dict[K, list[T]]`/`set` values, and `extend()`/`insert()`/`+=` on list-of-list targets; also whether dict/set literals have an analogous fixed-shape inference with the same missing coercion.
- **Verifier adjustment**: Problem is real and reproduced end-to-end: for 'outer: list[list[Int32]] = []; for i in range(3): inner = [i, i + 1]; outer.append(inner)', --dump-code emits 'std::array<int32_t, 2> inner = {i, (::tpy::add_check<int32_t>(i, 1))}; outer.push_back(std::move(inner));' inside std::vector<std::vector<int32_t>> nested(), and the full build fails with g++ 'error: no matching function for call to std::vector<std::vector<int>>::push_back(...std::array<int,2>...)'. Sema accepts the program; the Array-v...

#### B91. Nested container literal rejected: dict[str, list[T]] = {"k": [1]} fails with PendingList mismatch and a mislabeled diagnostic

- **Location**: tpyc/sema/expressions.py:2705 (_analyze_dict_literal)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-silent-copy`
- **Problem**: An annotated dict literal whose values are list literals is rejected: the expected-type context (value type list[Int32]) is not propagated into the nested list literal, leaving it as PendingList, and the compatibility check then fails. The diagnostic is also wrong twice over: it names the whole variable 'd' but prints the VALUE-slot types ("Type mismatch in variable 'd': expected list[Int32], got PendingList[Int32, 1]"). The same shape via `d = {}` then `d["k"] = [1]` compiles, so it is purely a literal-context propagation gap. dict.get's default arg has the same hole (`d.get("a", [])` -> "No matching overload for 'get' with argument types (str, PendingList[???, 0])").
- **Evidence**: Repro: d: dict[str, list[Int32]] = {"k": [1]} -> error: Type mismatch in variable 'd': expected list[Int32], got PendingList[Int32, 1]#0. Workaround compiles: d: dict[str, list[Int32]] = {}; d["k"] = [1]. Also: d.get("a", []) -> error: No matching overload for 'get' with argument types (str, PendingList[???, 0]#1).
- **Fix direction**: Propagate the annotated dict's value type as expected-type context into each value expression in _analyze_dict_literal (and key type into keys), resolving PendingList against it -- same mechanism list literals already use for their element context; also fix the diagnostic to name the offending value slot. Check set literals and call-arg default positions for the same missing context propagation.
- **Verifier adjustment**: Both repros reproduce byte-for-byte: `d: dict[str, list[Int32]] = {"k": [1]}` -> "av_f2_dictlit.py:4: error: Type mismatch in variable 'd': expected list[Int32], got PendingList[Int32, 1]#0"; `d.get("a", [])` -> "No matching overload for 'get' with argument types (str, PendingList[???, 0]#1)". The workaround (empty literal + subscript assign) compiles (verified in the Finding-1 repro). BUT the claimed mechanism and location are wrong: _analyze_dict_literal (tpyc/sema/expressions.py:2736) DOES...

#### B92. Mutating an element of a nested list literal (m[0].append) leaves inner/outer literals resolved as std::array -> C++ error

- **Location**: tpyc/sema/expressions.py:3404-3413, tpyc/sema/expressions.py:2875-2931 (TpyListRepeat sibling), tpyc/sema/local_deduction.py (gap-sweep)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-expr`
- **Problem**: The pending-list Array-vs-vector deduction marks a literal `is_mutated` only when mutation happens through the variable name directly. Mutation through a subscript chain (`m[0].append(1)`) marks neither the outer literal nor the inner element literals, so `m = [[0], [1]]` resolves to `std::array<std::array<int32_t,1>,2>` and the `.append` lowers to `push_back` on std::array -> C++ build failure on idiomatic Python. The list-repeat sibling `m = [[0]] * 2; m[0].append(1)` fails identically (repeat path at 2875 only sets needs_indexing, never element-mutation facts). Note _analyze_subscript already touches the ListLiteralInfo at 3404-3413 (needs_indexing for repeats) but has no hook for "element accessed mutably".
- **Evidence**: Repro /tmp/agents/exprrev/nested_mut.py: `m = [[0], [1]]; m[0].append(1); print(m)` -> generated `std::array<std::array<int32_t, 1>, 2> m = {{{0}, {1}}}; ::tpy::__getitem__(m, 0).push_back(1);` -> g++: "error: 'struct std::array<int, 1>' has no member named 'push_back'". Same error for `m = [[0]] * 2; m[0].append(1)` (listrep.py). CPython: both valid.
- **Fix direction**: When a method call's receiver is a subscript/field chain rooted at a pending-list variable, propagate the mutation to the literal: mark the root literal is_mutated AND, for nested literals, mark the element literal (ListLiteralInfo for inner pending types) so the element type resolves to list too. The deduction should treat "element handed out mutably via __getitem__" as mutation. Gap-sweep: same hole likely exists for `m[0] += [..]`, `for row in m: row.append(..)`, and dict/set-of-list literals.
- **Verifier adjustment**: Reproduced both shapes. `m = [[0],[1]]; m[0].append(1)` emits `std::array<std::array<int32_t,1>,2> m = {{{0},{1}}}; ::tpy::__getitem__(m, 0).push_back(1);`; full build confirms g++ error "'struct std::array<int, 1>' has no member named 'push_back'". `m = [[0]]*2; m[0].append(1)` emits the same array type via repeat_range and fails identically. The mechanism matches the cited code (subscript path at expressions.py:3404-3413 only sets needs_indexing; no element-mutation propagation to ListLiter...

#### B93. Int-literal unification keeps the first literal's value, losing range info: [0, 5000000000] resolves to Int32 -> C++ narrowing error

- **Location**: tpyc/sema/expressions.py:2681-2682 (_unify_literal_types), tpyc/sema/expressions.py:2128-2155 (array literal), tpyc/sema/expressions.py:2890-2905 (list repeat)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-expr`
- **Problem**: _unify_literal_types returns `a` (the first literal) when both element types are IntLiteralType, discarding the second literal's value. Downstream value-aware default-int selection (default_int_for_literal) then sees only the first value, so `[0, 5000000000]` picks Int32 and emits the 64-bit literal into an int32_t container -- C++ -Wnarrowing hard error on valid Python. Same root in the array-literal inferred-mode loop (the PendingListType element keeps the first IntLiteralType) and in _analyze_list_repeat (`continue` keeps first_type). A lone `x = 5000000000` resolves fine (value-aware), so this is specifically the multi-literal unification path. Affects list, set, and dict literals (set/dict reproduced via the same run).
- **Evidence**: Repro /tmp/agents/exprrev/biglit.py: `xs = [0, 5000000000]` and `s = {0, 5000000000}` -> g++: "error: narrowing conversion of '5000000000' from 'long int' to 'int' [-Wnarrowing]" on `std::array<int32_t, 2> xs = {0, 5000000000};` and `::tpy::ordered_set<int32_t>({0, 5000000000})`. CPython: valid (arbitrary-precision int).
- **Fix direction**: When unifying two IntLiteralTypes, keep the literal whose value has the larger magnitude (or track a [min,max] range on the literal type) so default-int selection covers all elements; alternatively re-validate each element's value against the chosen concrete element type at resolution time and emit a sema diagnostic. Apply uniformly to the list/set/dict/repeat paths.

#### B94. Ternary under a declared union annotation is rejected: `x: Int32 | str = 1 if flag else "x"`

- **Location**: tpyc/sema/expressions.py:2552-2575, tpyc/sema/expressions.py:2577-2644 (_ternary_common_type)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-expr`
- **Problem**: _analyze_if_expr propagates the type hint into each branch's analysis, but the join (_ternary_common_type) never consults the hint: the only union-producing rule is the None special case (T + None -> Optional[T]). With an explicit `Int32 | str` annotation whose members exactly cover the two branch types, the join raises "Incompatible types in ternary expression: 'Int32' and 'str'". Inconsistent with siblings: the statement form (`if flag: x = 1 else: x = "x"` against a declared union local) coerces each assignment independently and works, and a union-typed function-arg slot also accepts either member. The conditional expression is the only join that ignores the contextual union.
- **Evidence**: Repro /tmp/agents/exprrev/tern_union.py: `x: Int32 | str = 1 if flag else "x"` -> "tern_union.py:4: error: Incompatible types in ternary expression: 'Int32' and 'str'".
- **Fix direction**: In _analyze_if_expr, when a type_hint is present and both branch types are compatible with it (check_type_compatible per branch), use the hint as the common type and coerce each branch to it -- generalizing the existing None->Optional rule to arbitrary union/Optional/Any hints. Gap-sweep: verify the same hint-blind join doesn't affect `and`/`or` under a union annotation (it shares _normalize_pending_container but has no hint path at all).

### Theme: Calls, overloads, Callable/Fn values  (worst: high, 10 findings)

#### B95. Fn/Callable-typed calls synthesize is_readonly=True FunctionInfo, suppressing borrow-invalidation safety checks (UAF window)

- **Location**: tpyc/sema/calls.py:5436-5441, tpyc/sema/calls.py:2796-2798, tpyc/sema/calls.py:2845-2846, tpyc/sema/calls.py:2889-2890
- **Severity / category**: high / unsound-safety -- reproduced: yes -- found by `sema-calls`
- **Problem**: _analyze_typed_callable_call builds the resolved FunctionInfo with is_readonly=True even though an Fn/Callable contract says nothing about mutation. _check_borrow_arg_conflicts, _check_loop_var_arg_mutation and _record_mutation_call_edges all early-return on fi.is_readonly, so a callback that structurally mutates a by-reference container arg (append/clear) emits no 'Passing borrowed container' warning, no loop-var mutation marking, and no Phase-2 mutation call edge. An element borrow held across the callback call is silently invalidated -- the exact vector-reallocation UAF the warning system exists to catch -- and Phase-2 mutation propagation is blind through any function that forwards its param into a callback.
- **Evidence**: calls.py:5436-5441: `expr.resolved_function_info = FunctionInfo(name=func_label, params=[...], return_type=return_type, is_readonly=True)`. Repro: `def mutate(xs: list[P])->None: xs.append(P(99))` ; `def use_fn(f: Fn[[list[P]],None]): xs=[P(1),P(2)]; p=xs[0]; f(xs); print(p.v)` vs `use_direct` calling `mutate(xs)` directly. Observed: only the direct call warns (`fn_borrow.py:19: warning: Passing borrowed container 'xs' to non-readonly parameter 'xs' (function may invalidate references)`); the f(xs) call at line 13 produces no diagnostic.
- **Fix direction**: Synthesize the FunctionInfo with is_readonly=False and mutated_params=None (unknown => conservative), so the borrow-conflict warning and loop-var marking fire; for Phase-2, treat callable-typed callees as potentially-mutating sinks for their non-readonly reference params.

#### B96. Kwarg gap-fill self-matching inflates tier counts: wider, less-specific overload silently wins

- **Location**: tpyc/sema/overloads.py:605-615, tpyc/sema/overloads.py:87-99
- **Severity / category**: high / rejects-valid -- reproduced: yes -- found by `sema-calls`
- **Problem**: _expand_arg_types_with_kwargs fills defaulted positional gaps with the param's own type, claiming this 'does not skew cross-overload scoring'. It does: each gap-filled slot classifies as (EXACT_CONCRETE, 0), and _score compares raw per-tier COUNTS, so an overload with more defaulted params accumulates more exact-match counts and beats a strictly more specific overload with fewer params. Silently selects the wrong overload (wrong body executes, wrong return type), no diagnostic. Secondary hazard noted from reading (not reproduced): for a generic candidate, the gap fill inserts the raw TypeParamRef-bearing p.type into arg_types, which infer_type_params_for_function then matches against itself, potentially poisoning or failing inference.
- **Evidence**: Repro: `@overload def f(x: Int32, *, mode: str = "m") -> str: return "A"` and `@overload def f(x: Int32 | None, y: Int32 = 0, *, mode: str = "m") -> str: return "B"`; call `f(a, mode="t")` with `a: Int32`. Observed generated main: `std::cout << f(a, 0, "t")` -- only the SECOND overload is emitted/called (prints "B"), although overload A (exact Int32, declared first) is more specific. Score vectors: A = 2x EXACT, B = 3x EXACT (x via Optional-unwrap, gap-filled y self-match, mode).
- **Fix direction**: Exclude gap-filled (not-supplied) slots from the tier-count vector (score only user-supplied positions), or normalize scores by supplied-arg count. Also skip gap-fill insertion for TypeParamRef-typed params on generic candidates.

#### B97. Fn/Callable-typed calls discard the coercion: checked args are never wrapped, producing invalid C++

- **Location**: tpyc/sema/calls.py:5409-5442
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-calls`
- **Problem**: _analyze_typed_callable_call uses check_type_compatible purely as a predicate and never calls coerce_expr, so any arg that needs a registered coercion (BigInt->Int32 checked narrowing, str-family conversions, record->Ptr address-of, etc.) is emitted raw. Sema accepts the call, then the C++ build fails with a raw conversion error. Sibling asymmetry: direct function calls (_typecheck_call_args) wrap the same arg in TpyCoerce. Since `int` defaults to BigInt, passing any plain int local to a callback declared Fn[[Int32], None] is mainstream and breaks. Same path also skips check_own_param / _restore_readonly_arg, so the Own-consumption checks direct calls get are absent at the callable boundary (sibling gap for the gap-sweep: method-valued callables via __call__ go through methods.py instead and were not checked here).
- **Evidence**: calls.py:5423-5434: `arg_type = self.expr.analyze_expr_with_hint(arg, expected_type)\n if arg_type != expected_type:\n try:\n self.compat.check_type_compatible(...)\n except SemanticError: raise ...` -- return value (the Coercion) is dropped; expr.args never rewritten. Repro: `def cb(x: Int32)->None: print(x)` ; `def use(f: Fn[[Int32],None])->None: n: int = 5_000_000_000; f(n)`. `uv run tpy` output: `error: cannot convert 'tpy::BigInt' to 'int' in argument passing  f(n);` -- whereas `cb(n)` directly emits the checked BigInt->Int32 coercion.
- **Fix direction**: Route per-arg handling through the same _typecheck_and_coerce_arg helper direct calls use (coerce_expr + check_own_param + _restore_readonly_arg), storing the coerced node back into expr.args.

#### B98. Union[A,B] arg accepted where Union[A,B,C] expected with no variant conversion -- C++ build fails

- **Location**: tpyc/sema/compatibility.py:637-643
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-calls`
- **Problem**: _check_compat's Union->Union rule checks each actual member against the expected union and returns None (no coercion). C++ std::variant has no converting constructor from a different variant instantiation, so passing a narrower union (param, local, return) into a wider union slot compiles in sema and dies in g++ with an invalid-reference-initialization error. Union widening at a call boundary is ordinary Python typing; there is no clean workaround short of manual isinstance re-dispatch. Same family as the tracked storage-vs-borrow variant gaps in BUGS.md (lines 24, 51) but a distinct, untracked member-set-widening case. Interacts with Optional unions and pointer-variant unions, which I did not separately exercise -- gap-sweep should check `A|None -> A|B|None` and reference-type pointer-variants.
- **Evidence**: Repro: `def f(x: Int32 | str | float)->None: print(x)` ; `def g(y: Int32 | str)->None: f(y)`. Generated: `void g(const std::variant<int32_t, std::string>& y) { f(y); }` with `void f(const std::variant<int32_t, double, std::string>& x)`. Build: `error: invalid initialization of reference of type 'const std::variant<int, double, ...>&' from expression of type 'const std::variant<int, ...>'`. (The same repro also tripped the already-tracked BUGS.md:366 union-print failure.)
- **Fix direction**: Return a coercion (variant re-wrap visitor: std::visit the source and construct the wider variant) from the Union->Union branch, or reject in sema with an actionable diagnostic until codegen supports the re-wrap. Must consider both value-variant and pointer-variant forms.

#### B99. T matching Optional[T] param scores the same tier+cost as exact T -- exact/Optional overload pairs are 'ambiguous'

- **Location**: tpyc/sema/overloads.py:285-289
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-calls`
- **Problem**: _classify_strict_match handles `T -> Optional[T]` by recursing into param_inner.inner and returning the inner tier unchanged, so f(x: Int32) and f(x: Int32 | None) both classify a plain Int32 arg as (EXACT_CONCRETE, 0). resolve_overload sees a genuine tie of distinct signatures and raises OverloadAmbiguityError, rejecting code every Python type checker accepts (the non-Optional overload is more specific). Same un-penalized Optional unwrap is also what let the wider overload tie per-arg in the gap-fill finding.
- **Evidence**: Repro: `@overload def f(x: Int32) -> str: return "exact"` / `@overload def f(x: Int32 | None) -> str: return "optional"`; call `f(a)` with `a: Int32 = 5`. Observed: `ovl_optional.py:14: error: Ambiguous overload for 'f': multiple candidates match equally: f(Int32); f(Int32 | None)`.
- **Fix direction**: Add a widening cost (or a dedicated lower tier) for the Optional-unwrap step in _classify_strict_match so exact matches dominate, mirroring how IntLiteral widening uses cost.

#### B100. Strict overload matching unwraps readonly from the arg, so readonly/mutable overload pairs are 'ambiguous' (and mutable-only overloads strict-match readonly args)

- **Location**: tpyc/sema/overloads.py:249-252, tpyc/sema/overloads.py:481-484
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-calls`
- **Problem**: _classify_strict_match (and type_matches_with_coercion) strips ReadonlyType from BOTH arg and param before comparing, so a readonly[list[Int32]] arg counts as EXACT_CONCRETE against a mutable list[Int32] param -- a match that the commit-side _check_compat would reject as const-laundering. With both a readonly and a mutable overload declared, a readonly arg ties both and resolution refuses with an ambiguity error instead of selecting the only overload that can actually accept the arg. With only the mutable overload, resolution selects it and the user gets the later 'Cannot pass readonly ... as mutable' error even if another viable shape existed. Also note the diagnostic renders the mutable param's internal Ref wrapper ('Ref[list[Int32]]'), leaking an internal type name.
- **Evidence**: Repro: `@overload def total(xs: readonly[list[Int32]]) -> Int32: ...` / `@overload def total(xs: list[Int32]) -> Int32: xs.append(0); ...` ; `def caller(xs: readonly[list[Int32]]) -> None: print(total(xs))`. Observed: `ovl_readonly2.py:17: error: Ambiguous overload for 'total': multiple candidates match equally: total(readonly[list[Int32]]); total(Ref[list[Int32]])`.
- **Fix direction**: Make readonly flow-sensitive in matching: a readonly arg must not strict-match a non-readonly reference-type param (reject or down-tier it); a mutable arg matching a readonly param is fine (adding const). Mirrors the is_readonly_receiver pre-filter that methods already get.

#### B101. Callable->Fn parameter variance is backwards: unsound covariant accept, sound contravariant reject

- **Location**: tpyc/sema/compatibility.py:849-856
- **Severity / category**: medium / unsound-safety -- reproduced: yes -- found by `sema-calls`
- **Problem**: The Callable->Fn branch checks `_check_compat(a, e)` for each (actual_param, expected_param) pair -- a covariant check. Function parameters are contravariant: a Callable[[Int32], None] must NOT satisfy Fn[[Int32 | None], None] (the Fn contract may pass None), yet _check_compat(Int32, Int32|None) succeeds via the T->Optional rule and sema accepts. The error is then a C++ requires-clause failure, not a TPy diagnostic. Symmetrically, the sound direction (Callable[[Int32 | None], None] into Fn[[Int32], None]) is rejected because _check_compat(Int32|None, Int32) fails. Return types should be covariant (current direction is right for returns).
- **Evidence**: compatibility.py:852-853: `all(self._check_compat(a, e, "param", loc) is None for a, e in zip(actual.param_types, expected.param_types))`. Repro: `def cb(x: Int32)->None: ...; def use(f: Fn[[Int32|None],None])->None: f(None)`; `g: Callable[[Int32],None] = cb; use(g)`. `--dump-code` succeeds (sema accepts); full build fails: `error: no matching function for call to 'use(std::function<void(int)>&)'` (concept mismatch).
- **Fix direction**: Swap the per-param check direction (`_check_compat(e, a)`) so params are contravariant; keep the return check covariant. Audit the sibling exact-equality CallableType branch at compatibility.py:491-501 and the __call__-record branch (838-841) for the same variance question.

#### B102. Recursive-union constructor type error crashes the compiler (TpyCall passed as SourceLocation)

- **Location**: tpyc/sema/calls.py:4938
- **Severity / category**: medium / crash -- reproduced: yes -- found by `sema-calls`
- **Problem**: _analyze_recursive_union_constructor passes the call node positionally into check_type_compatible's `loc` parameter: `check_type_compatible(arg_type, union_type, "argument 'value'", expr)`. On an incompatible argument, CompatError carries the TpyCall as loc, and SemanticError.format does `self.loc.line` -> AttributeError traceback instead of a clean diagnostic. Additionally source_expr is None on this path, so the polymorphic-slicing guard and Own-copy warnings that other arg paths run are skipped, and the returned Coercion is discarded (the wrapper ctor presumably relies on codegen-side wrapping; flagging for the gap-sweep).
- **Evidence**: Repro: `class Leaf: ...` ; `type Tree = Leaf | list[Tree]` ; `t = Tree(3.5)`. Observed: `AttributeError: 'TpyCall' object has no attribute 'line'` (traceback from diagnostics.py:93 `f"{name}:{self.loc.line}: ..."`).
- **Fix direction**: Pass `loc=expr.loc, source_expr=arg` (and apply the returned coercion via coerce_expr for consistency with other constructor paths).

#### B103. Record with multiple __call__ overloads passed to an Fn slot hits a bare assert ('Internal error')

- **Location**: tpyc/sema/compatibility.py:836
- **Severity / category**: medium / crash -- reproduced: yes -- found by `sema-calls`
- **Problem**: The callable-object->Fn branch asserts `len(overloads) == 1` (`multiple __call__ overloads not supported`). A user record with two @overload __call__ methods -- otherwise legal -- aborts compilation with 'Internal error: multiple __call__ overloads not supported' (an AssertionError, no source location) the moment an instance flows into an Fn/Callable-typed slot. Should be a located SemanticError, or better, overload selection against the expected signature (one of the overloads may match exactly).
- **Evidence**: compatibility.py:836: `assert len(overloads) == 1, f"multiple __call__ overloads not supported"`. Repro: `class Adder:` with two `@overload def __call__` methods, `def use(f: Fn[[Int32], Int32]) -> None: ...`, `use(Adder())`. Observed: `Internal error: multiple __call__ overloads not supported` (no file:line).
- **Fix direction**: Replace the assert with overload selection: pick the __call__ overload whose signature matches `expected`; raise a located SemanticError listing candidates when none/ambiguous.

#### B104. Callable-typed field calls record no mutation facts and synthesize is_readonly=True -- mutating callbacks die in std::function template noise

- **Location**: tpyc/sema/methods.py:570-629
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-methods-protocols`
- **Problem**: _try_callable_field_call builds a synthetic FunctionInfo with is_readonly=True and returns before _resolve_and_check_args, so no mutation call edges, no check_own_param, no _check_borrow_arg_conflicts run for the call. Consequences observed: (1) args passed to the callable are never marked mutated, so a field like data: list[Int32] passed to self.cb stays 'unmutated' and the enclosing method is const-inferred (infer_method_const) -- generated 'void poke() const'; (2) the Callable field's std::function signature is emitted with const params, so assigning a CPython-valid mutating callback (xs.append) fails with a multi-screen std::function overload dump instead of a TPy diagnostic. Mutation-by-callback is thus unrepresentable, and the failure mode is cryptic. Sibling gap for the gap-sweep: direct calls of Callable locals/params go through calls.py and may share the missing-edge problem.
- **Evidence**: Repro:
  class Holder:
      data: list[Int32]
      cb: Callable[[list[Int32]], None]
      def poke(self) -> None: self.cb(self.data)
  with cb = add_one where 'def add_one(xs): xs.append(1)'.
Dump: 'void poke() const;'. Build: 'no known conversion for argument 1 from void(std::vector<int>&) to const std::function<void(const std::vector<int>&)>&' (plus ~30 lines of candidates). CPython runs this fine.
Code: methods.py:622-627 'expr.resolved_function_info = FunctionInfo(..., is_readonly=True)'; the early returns at 565-567/629 bypass _record_mutation_call_edges.
- **Fix direction**: Decide and document Callable mutability: either (a) CallableType params carry readonly/mut markers and sema rejects storing a mutating function into a readonly-param Callable with a clean diagnostic at the assignment site, or (b) treat Callable-field calls conservatively (unknown callee: all reference args mutated, receiver not readonly) and emit non-const std::function signatures. Either way, route the call through the mutation-edge recording used by every other call shape.

### Theme: Readonly system holes  (worst: high, 3 findings)

#### B105. readonly silently lost through tuples: readonly[tuple[...]] element mutates caller's object

- **Location**: tpyc/sema/expressions.py:3312-3314 (unwrap_readonly before _analyze_tuple_subscript), 3259-3288 (_analyze_tuple_subscript returns raw element type)
- **Severity / category**: high / unsound-safety -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: Subscripting a readonly tuple strips ReadonlyType (actual_for_tuple = unwrap_readonly(inner_obj_type)) and _analyze_tuple_subscript returns the bare element type with no readonly re-wrap -- unlike the list/protocol/record subscript paths (3411, 3420, 3429) which all wrap non-value elements in ReadonlyType. Tuple unpacking (a, b = t) has the same gap. Crucially there is NO C++ backstop: the borrow-form mapping of readonly[tuple[Point, Int32]] is 'const std::tuple<Point*, int32_t>&' -- shallow const over a mutable Point* -- so the emitted write compiles and mutates the caller's object through a readonly reference, silently. This violates the core readonly contract table (subscript read on readonly returns readonly[Elem]) and the docs' claim that readonly cannot be laundered. Sibling note: any other borrow form carrying raw pointers under top-level const (e.g. tuples nested in readonly containers, iterator tuple yields) inherits the hole.
- **Evidence**: Repro /tmp/agents/r14b_ro_tuple_local.py: def f(t: readonly[tuple[Point, Int32]]): t[0].x = 5; main: pt = Point(1); t = (pt, 2); f(t); print(pt.x). Compiles with zero diagnostics, C++ 'void f(const std::tuple<Point*, int32_t>& t) { std::get<0>(t)->x = 5; }', runs and prints 5 (mutated; readonly contract requires rejection). Unpack variant /tmp/agents/r15_ro_tuple_unpack.py ('a, b = t; a.x = 6') likewise prints 6.
- **Fix direction**: In _analyze_tuple_subscript (and the tuple-unpack path), when the subscripted object type is ReadonlyType, wrap non-value element results in ReadonlyType, matching the list/protocol/record paths. Additionally consider mapping readonly tuple borrow form to const-pointer elements (std::tuple<const Point*, ...>) so C++ provides defense in depth.
- **Verifier adjustment**: Bug is real exactly as described, but severity is high, not critical. Verified code: expressions.py:3312-3314 strips readonly (actual_for_tuple = unwrap_readonly) before _analyze_tuple_subscript, which returns the raw element type at 3287 with no ReadonlyType re-wrap; the sibling list (3411-3412), protocol (3420-3421), and record (3429-3430) subscript paths all re-wrap non-value elements in ReadonlyType. Reproduced /tmp/agents/verify/r14_ro_tuple.py: 'def f(t: readonly[tuple[Point, Int32]]):...

#### B106. readonly[dict] subscript does not propagate ReadonlyType (list does) -- C++ errors instead of TPy diagnostics

- **Location**: tpyc/sema/expressions.py:3349-3367 (PendingDict + is_dict paths), 3322-3346 (TypedDict path); contrast 3411-3412
- **Severity / category**: medium / unsound-safety -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: The dict subscript-read path returns make_ref(v_type) without the 'if is_readonly_obj: wrap ReadonlyType' step that the integer-index container path applies. Sema therefore accepts element mutation through a readonly dict (d["k"].x = 9, d["k"].bump(), passing d["k"] to a mutable param); only the C++ const system catches it, with cryptic g++ errors ('assignment of read-only location', 'discards qualifiers') instead of the proper 'Cannot mutate readonly reference' that list[T] produces. Same gap in the TypedDict and PendingDict branches. Direct subscript WRITE (d["k"] = v) is still rejected by the assignment-target check, so the gap is read-then-mutate only. Beyond UX, any sema-level logic keyed on readonly-ness (narrowing preservation, mutation propagation) sees the element as mutable. Sibling note for gap-sweep: set/bytearray and user __getitem__ with non-int keys route differently and were not all probed.
- **Evidence**: Repro /tmp/agents/r6c_dict.py: def h(d: readonly[dict[str, Point]]): d["k"].x = 9 -- zero TPy diagnostics; C++ build fails with 'assignment of read-only location'. /tmp/agents/r21_ro_dict_method.py (d["k"].bump()) likewise passes sema, fails C++ with 'passing const Point as this argument discards qualifiers'. The list equivalent (r6_container.py) is correctly rejected by sema: 'Cannot mutate readonly reference'.
- **Fix direction**: Mirror the int-path wrap in all three dict-shaped subscript returns: if is_readonly_obj (compute it before the dict branches) and the value type is non-value, return make_ref(ReadonlyType(v_type)).

#### B107. enumerate() rejects readonly containers (stub takes mutable Iterable[T]) -- read-only iteration impossible

- **Location**: lib/tpy/tpy/_builtins/_funcs.py:575-581
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: The enumerate stub declares 'iterable: Iterable[T]' without readonly, so 'for i, p in enumerate(xs)' on xs: readonly[list[Point]] fails with 'Cannot pass readonly[list[Point]] as mutable Iterable[Point]' -- even for pure reads. Direct 'for p in xs' on a readonly list works (yields readonly elements), so the most idiomatic indexed-read loop over a readonly container is rejected. Workaround: range(len(xs)). zip/reversed/sorted and other Iterable-taking builtins likely share the gap (flag for gap-sweep). Note the interaction with the readonly-tuple finding: once enumerate accepts readonly sources, its tuple yield must propagate readonly to the element or it becomes another laundering path.
- **Evidence**: Repro /tmp/agents/r22_enumerate.py: def g(xs: readonly[list[Point]]): for i, p in enumerate(xs): p.x = 7 -> 'error: Cannot pass readonly[list[Point]] as mutable Iterable[Point] in argument iterable' (the rejection fires on the enumerate call itself, before the body is even considered).
- **Fix direction**: Give enumerate (and the Iterable-taking read-only builtins) readonly-polymorphic signatures -- e.g. @readonly / readonly[Iterable[T]] overloads or @auto_readonly-style duality -- and ensure the yielded tuple element carries readonly[T] for readonly sources.

### Theme: Methods, inheritance, protocols  (worst: high, 5 findings)

#### B108. Iterator-invalidation borrow check misses mutating METHOD calls on the owning object while iterating its field

- **Location**: tpyc/sema/methods.py:784-802, tpyc/sema/methods.py:141-182
- **Severity / category**: high / unsound-safety -- reproduced: yes -- found by `sema-methods-protocols`
- **Problem**: The borrow-conflict check in analyze_method_call only fires when the method receiver's own storage key has an element borrow. Iterating 'w.items' registers the borrow on storage 'w.items', but calling 'w.add()' (a method that does self.items.append) has receiver storage 'w' -- never related to 'w.items' -- so no warning is emitted and the generated C++ is a textbook vector-reallocation-during-range-for UAF. The direct sibling 'w.items.append()' in the same loop DOES warn, so the hole is specifically methods on the owner. The indirect variant (w.add2() calling self.add()) is additionally missed because _is_invalidating_method only consults direct_structural_mutated_params (Phase-1 facts; the comment at methods.py:173-175 admits Phase-2 structural propagation is not consulted). Both variants compile silently to UB-prone C++.
- **Evidence**: Repro:
  class W:
      items: list[Int32]
      def add(self) -> None: self.items.append(3)
      def add2(self) -> None: self.add()
  def direct(w: W) -> None:
      for x in w.items:
          w.add(); print(x)
Observed: zero warnings for both direct(w.add) and indirect(w.add2). Baseline control: 'for x in xs: xs.append(4)' warns 'Mutation of xs while iterating over it', and 'for x in w.items: w.items.append(3)' warns too -- only the owner-method form escapes.
- **Fix direction**: When the receiver's record type structurally mutates self (structural_mutated_params/direct contains -1), treat every borrowed storage rooted under the receiver (receiver_key + '.*') as conflicting -- i.e. relate has_element_borrow to field paths of the receiver, not just the receiver's own key. Use Phase-2 structural_mutated_params (not only direct_*) once available; for Phase-1-ordering reasons consider a deferred check like the existing deferred borrow checks. Gap-sweep: same hole presumably exists for dict/set fields and for free functions taking the owner by mutable ref.

#### B109. By-value field referencing a later-defined record passes sema, emits incomplete-type C++

- **Location**: tpyc/sema/registration.py:879-918 (field validation), tpyc/sema/analyzer.py:543-692 (registration ordering)
- **Severity / category**: medium / crash -- reproduced: yes -- found by `sema-core`
- **Problem**: Registration validates field types against the registry (placeholders make forward refs resolve), but nothing checks that a by-value (inline storage) field's type is emittable before the enclosing record, and codegen emits structs in definition order. `class A: b: B` with B defined later passes sema with zero diagnostics and produces C++ that fails with 'field b has incomplete type B'. Acyclic forward refs are not caught by detect_type_cycles (no cycle), so the user gets an opaque C++ error for code the compiler accepted.
- **Evidence**: nocopy_order2.py (class A with field `b: B`, B defined after A): tpy --dump-code produces no diagnostics; build fails: "error: field 'b' has incomplete type 'tpyapp::nocopy_order2::B'" with note 'forward declaration of struct B'.
- **Fix direction**: Either topologically order struct emission by by-value field dependencies (cycle detection already proves acyclicity), or reject by-value forward field references at registration with a sema diagnostic pointing at the field. Codegen emission order is outside my assigned files -- gap-sweep should decide which side owns the fix.

#### B110. 3-level generic inheritance: inherited method types dangle (Ref[U]) -- missing transitive TypeParamRef resolution

- **Location**: tpyc/sema/methods.py:1713-1726, tpyc/sema/protocols.py:1105-1118
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-methods-protocols`
- **Problem**: lookup_record_method_overloads composes parent substitutions as {**parent_subst, **type_subst} without resolving chains, and _analyze_instance_method only resolves inherited_subst values through instance_subst (the leaf's own args). For Base[T].get()->T, Mid[U](Base[U]), Child(Mid[Int32]): the merged subst is {T: U, U: Int32}; substituting get's return type yields the dangling TypeParamRef U, so c.get() types as Ref[U] and downstream use errors. The protocol-conformance sibling type_has_method_with_signature has an explicit transitive-resolution loop for exactly this (protocols.py:721-729, 'Base.T->Mid.U->Child.V->Int32'), and lookup_record_field substitutes per level on the unwind -- only the method-call dispatch path lacks it.
- **Evidence**: Repro:
  class Base[T]:
      def get(self) -> T: return self.val
  class Mid[U](Base[U]): ...
  class Child(Mid[Int32]): ...
  c = Child(7); print(c.get() + 1)
Observed: 'inherit3.py:21: error: Invalid operand types for '+': Ref[U] and IntLiteral(1)'. 2-level chains work (instance_subst covers them).
- **Fix direction**: Apply the same transitive TypeParamRef chain resolution from protocols.py:721-729 to the type_subst built in _analyze_instance_method (or resolve inside lookup_record_method_overloads when combining parent_subst with type_subst, which would fix all consumers at once -- super(), unbound-self, and static dispatch paths share it).

#### B111. Inherited @property from a generic parent never substitutes type params (broken at 2 levels)

- **Location**: tpyc/sema/protocols.py:1075-1087
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `sema-methods-protocols`
- **Problem**: lookup_record_property recurses through parents but, unlike lookup_record_field (which substitutes via get_parent_type_subst on each unwind, lines 1016-1023) and lookup_record_method_overloads (which returns a subst), it returns the inherited PropertyInfo verbatim with no type-param substitution. A property 'def v(self) -> T' on Base[T] accessed through Child(Base[Int32]) types as Ref[T]. Sibling inconsistency: fields substitute correctly at any depth, methods at depth 2, properties never.
- **Evidence**: Repro:
  class Base[T]:
      @property
      def v(self) -> T: return self._v
  class Child(Base[Int32]): ...
  c = Child(5); print(c.v + 1)
Observed: 'prop_inherit.py:18: error: Invalid operand types for '+': Ref[T] and IntLiteral(1)'.
- **Fix direction**: Mirror lookup_record_field: when a property is found on a parent, build get_parent_type_subst(parent_type, parent_info) and substitute the property's getter/setter types before returning (and compose substitutions on multi-level unwinds, with the transitive resolution from the previous finding).

#### B112. Structural protocol conformance admits SUPERTYPE return types; violation surfaces as cryptic C++ 'constraints not satisfied'

- **Location**: tpyc/sema/protocols.py:814-819
- **Severity / category**: medium / unsound-safety -- reproduced: yes -- found by `sema-methods-protocols`
- **Problem**: _protocol_type_matches contains 'if self.ctx.registry.is_subclass_of(expected, unwrapped): return True' on the return-type path -- i.e. an implementation whose method returns a SUPERTYPE of what the protocol requires is accepted as conforming (covariance inverted). The comment justifies it for inherited __iter__ returning a parent iterator checked against '-> Self', but the arm applies to every method/return. Sema then monomorphizes the call, types the result as the protocol's (narrower) type, and the C++ concept check is the only thing that stops the miscompile -- the user gets 'template argument deduction/substitution failed: constraints not satisfied' instead of a TPy conformance diagnostic listing the wrong signature.
- **Evidence**: Repro:
  class DogMaker(Protocol):
      def make(self) -> Dog: ...
  class Shelter:
      def make(self) -> Animal: return self.pet   # Animal is Dog's PARENT
  def use(m: DogMaker) -> Int32:
      d = m.make(); return d.tricks
  use(Shelter())
Sema: exit 0, emits 'Dog& d = m.make();' inside 'template<class T_m> requires DogMaker<T_m>'.
C++ build: 'error: no matching function for call to use(Shelter&) ... constraints not satisfied'.
- **Fix direction**: Restrict the supertype-return admission to the documented case: only when the protocol's expected type came from a Self substitution (track that in the substitution pass), or when actual is the unparameterized self class of an inherited method. Otherwise return False so get_protocol_conformance_issues produces the clean 'wrong signature' diagnostic.

### Theme: Cross-module identity by short name  (worst: medium, 3 findings)

#### B113. error_return type identity is bare-name only: distinct same-named exceptions from different modules conflated

- **Location**: tpyc/typesys.py:202-211, tpyc/typesys.py:214-225
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `typesys`
- **Problem**: error_return_matches() compares bare_name(a) == bare_name(b), deliberately ignoring the module prefix (to tolerate re-export qualification differences like tplib.json.JsonError vs tplib.json.parser.JsonError). But this conflates genuinely distinct exception types that share a short name across modules: sema accepts auto-propagation of mod_x.AppError out of a function declared @error_return(mod_y.AppError) and emits a direct `return f(...)` between incompatible std::expected types, failing the C++ build with a confusing template error instead of a sema diagnostic. is_return_exception() (lines 214-225) has the same bare-name semantics: register_return_exception stores bare names in a compilation-global set, so ANY class named e.g. 'StopIteration' anywhere is treated as ReturnException. Same-short-name exceptions ('error', 'ParseError') across modules are mainstream (stdlib itself has re.error). Gap-sweep: the exact-string comparison sites in sema/statements.py that the qualify_exception_name docstring mentions rely on both sides producing identical strings -- the bare-name fallback undermines that contract for the cross-module case.
- **Evidence**: Repro: mod_x defines `class AppError(Exception, ReturnException)` + `@error_return(AppError) def f(...)`; mod_y defines its own AppError(Exception, ReturnException); main imports mod_y's AppError and declares `@error_return(AppError) def g(flag): return mod_x.f(flag)`. tpyc accepts; generated `std::expected<int32_t, ::tpyapp::mod_y::AppError> g(bool flag) { return ::tpyapp::mod_x::f(flag); }`. g++: "error: could not convert 'tpyapp::mod_x::f(bool)()' from 'expected<[...],tpyapp::mod_x::AppError>' to 'expected<[...],tpyapp::mod_y::AppError>'".
- **Fix direction**: Compare canonical defining-module qnames (resolve both sides through the registry's RecordInfo.defining_module, which qualify_exception_name already computes for re-exports) instead of bare names; keep a re-export-aware equivalence (same RecordInfo identity) rather than string suffix match. Keep return_exception_names keyed by qname.

#### B114. Union of two same-canonical-name classes from different modules silently collapses to one member

- **Location**: tpyc/typesys.py:3397-3407, tpyc/typesys.py:905-913, tpyc/parse/type_resolver.py:321
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `typesys`
- **Problem**: NominalType equality includes _module_qname precisely so 'pkg_a.Foo vs pkg_b.Foo never collapse via set/dict dedup' (comment at typesys.py:908-911). But at annotation-resolution time the parser feeds make_union placeholder NominalTypes whose _module_qname is None; two records imported from different modules that share a canonical declaration name (e.g. `from mod_a import Thing; from mod_b import Thing as ThingB`) resolve to placeholders that compare EQUAL (name='Thing', qname=None on both -- the alias resolves to the canonical record name), so make_union's set-dedup collapses `Thing | ThingB` to a single NominalType. The function param is then typed as one class only and calls with either class fail with the absurd diagnostic 'expected Thing, got Thing' / 'expected Thing, got ThingB'. Same name-only-identity family: coercions.py record_to_ptr/record_to_const_ptr match `rec.name == ptr.pointee.name` (coercions.py:453-455, 467-468), and same_nominal_symbol_loose-based is_subclass_of matches permissively whenever either side is bare -- gap-sweep should check those paths for cross-module same-name confusion too.
- **Evidence**: Repro: mod_a.py and mod_b.py each define `class Thing`. main.py: `from mod_a import Thing; from mod_b import Thing as ThingB; def show(v: Thing | ThingB): ...; show(Thing(7))` -> 'error: Type mismatch in argument v: expected Thing, got Thing'; `show(ThingB(9))` -> 'expected Thing, got ThingB'. API confirmation: NominalType('Thing') == NominalType('Thing') with both qnames None; make_union(p1, p2) returns a single NominalType, not a union.
- **Fix direction**: Mint _module_qname on annotation-resolution placeholders before union construction (the import table knows the source module), or defer make_union canonicalization until sema has resolved qnames. At minimum, sema's alias-finalize pass should re-canonicalize parser-built unions from resolved member types.

#### B115. record_to_ptr coercion matches records by short name only

- **Location**: tpyc/coercions.py:449-474
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `typesys`
- **Problem**: record_to_ptr / record_to_const_ptr type_match compares `rec.name == ptr.pointee.name` -- short-name string equality with no qname check. Two distinct user records sharing a short name across modules would satisfy the coercion in sema (taking &value of one class into a Ptr of the other), with the error surfacing only as a confusing C++ namespace mismatch at build time. Same name-only-identity family as the union-collapse finding; not separately reproduced end-to-end. ptr_to_const_ptr by contrast compares full pointee types with ==.
- **Evidence**: type_match=lambda rec, ptr: (isinstance(ptr.pointee, NominalType) and ptr.pointee.is_user_record and rec.name == ptr.pointee.name)  # coercions.py:453-455
- **Fix direction**: Compare qualified names (rec.qualified_name() == ptr.pointee.qualified_name()) with a bare-side fallback only when one side is a placeholder, mirroring same_nominal_symbol_loose.

### Theme: C++ identifier / namespace hygiene in emitted code  (worst: high, 6 findings)

#### B116. User global named 'initialized' silently shadowed by __tpy_init guard

- **Location**: tpyc/codegen_cpp/functions.py:1836-1841
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `codegen-funcs-records`
- **Problem**: gen_module_init emits `static bool initialized = false; if (initialized) return; initialized = true;` as the double-init guard inside __tpy_init(), where all top-level statements are also emitted. A user module-level variable named `initialized` is declared at namespace scope, but every top-level read/write of it inside __tpy_init binds to the guard local instead. The user's global is never initialized (functions reading it see the zero-initialized namespace global), and a user write of False re-arms the guard, allowing double initialization in diamond imports. No diagnostic; output silently diverges from CPython.
- **Evidence**: Repro /tmp/agents/fnrev/b4.py:
```python
initialized = True
def show() -> None:
    print(initialized)
show()
```
CPython prints `True`; `uv run tpy b4.py` prints `False`. Generated code (b2 variant):
```cpp
bool initialized{};            // namespace-scope user global
void __tpy_init() {
    static bool initialized = false;   // guard shadows the global
    if (initialized) return;
    initialized = true;
    initialized = false;   // user's top-level assignment hits the guard
    ...
}
```
- **Fix direction**: Rename the guard to a reserved name (e.g. `__tpy_initialized_`), consistent with the `__tpy_` prefix convention used elsewhere. Check sibling synthesized locals in module-init for the same class (e.g. the `main();` tail call vs a user global named main).

#### B117. Global variable declarations skip escape_cpp_name; reads use the escaped name

- **Location**: tpyc/codegen_cpp/functions.py:1769, tpyc/codegen_cpp/functions.py:1771, tpyc/codegen_cpp/functions.py:1779, tpyc/codegen_cpp/functions.py:1781, tpyc/codegen_cpp/functions.py:1803, tpyc/codegen_cpp/functions.py:1806, tpyc/codegen_cpp/functions.py:1819
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-funcs-records`
- **Problem**: gen_global_decl / gen_global_extern / gen_final_global_header / gen_final_global_source all write `stmt.name` raw, while expression codegen escapes reads via escape_cpp_name. A module-level variable named after a C++ keyword (`new`, `template`, `class`, ...) produces (a) an invalid C++ declaration and (b) a name mismatch with the escaped read sites, so even fixing one side alone would leave undefined symbols. Fields, params and methods all escape correctly -- globals are the missed sibling.
- **Evidence**: Repro: `new = 5` / `template = 7` at module level produces:
```cpp
extern int32_t new;
extern int32_t template;     // header
int32_t new{};
int32_t template{};          // source
... ::tpy::add_check<int32_t>(new_, template_)   // reads use escaped names
```
Invalid C++ (keyword as identifier) plus declared/used name mismatch.
- **Fix direction**: Route the declaration name through escape_cpp_name in all four global-emission helpers (and audit gen_module_init's global_types pre-seed for the same).

#### B118. Synthesized operator wrappers and @dynamic virtual/adapter signatures emit unescaped param names

- **Location**: tpyc/codegen_cpp/records.py:1549-1565, tpyc/codegen_cpp/records.py:1580-1591, tpyc/codegen_cpp/records.py:1603-1610, tpyc/codegen_cpp/protocols.py:853-858, tpyc/codegen_cpp/protocols.py:820, tpyc/codegen_cpp/protocols.py:671
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-funcs-records`
- **Problem**: The synthesized C++ wrappers -- operator[] (_gen_const/_gen_mutable_subscript_operator), friend binary operators (_gen_binary_operators), operator() (_gen_call_operator) -- and the @dynamic protocol path (gen_dynamic_base_class virtuals, _gen_adapter_overrides, via _dynamic_param_list) all emit the Python parameter name raw, without escape_cpp_name. A dunder or protocol-method parameter named after a C++ keyword breaks compilation even though the underlying method itself escapes correctly.
- **Evidence**: `def __getitem__(self, new: int)` generates:
```cpp
::tpy::BigInt __getitem__(const ::tpy::BigInt& new_) const;   // method: escaped
::tpy::BigInt operator[](::tpy::BigInt new) const { return __getitem__(new); }  // invalid
```
`def __add__(self, double: 'Vec')` -> `friend ... operator+(const Vec& lhs, const Vec& double)`; `def __call__(self, char: int)` -> `operator()(const ::tpy::BigInt& char)`. @dynamic `def add(self, new: int)` -> `virtual ::tpy::BigInt add(const ::tpy::BigInt& new) = 0;` and the same in both Adapter overrides.
- **Fix direction**: Apply escape_cpp_name at each wrapper emission site (param name only -- protocol T_<pname> template names are deliberately unescaped per protocols.py:39-51 and must stay consistent with the coro frame sites).

#### B119. User function named `run` breaks asyncio.run resolution: emitted as `::tpyapp::__main__::run<T>` -- nonexistent namespace

- **Location**: tpyc/codegen_cpp/expressions.py (cross-module call emission), repro /tmp/agents/bf/z_run_collision.py
- **Severity / category**: low / rejects-valid -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: Off-focus bonus found while building an async repro. `import asyncio` plus a module-level `async def run(...)`, called as `asyncio.run(run())`, emits the asyncio.run call with the asyncio-run SHAPE (template arg + make_adapter<Cancellable<T>> wrapper) but the USER function's qualified name rendered as `::tpyapp::__main__::run<int32_t>(...)` -- the `tpyapp::__main__` namespace does not exist (the module's actual namespace is tpyapp::<filename>), and even the correct user namespace would be the wrong callee. An explicit module-attribute call `asyncio.run(...)` must never resolve through the calling module's own symbol of the same name. Raw g++ error ('tpyapp::__main__ has not been declared'), no TPy diagnostic. `run` is a natural name for the main coroutine, so users will hit this.
- **Evidence**: Repro /tmp/agents/bf/z_run_collision.py: `async def run() -> Int32: ...; asyncio.run(run())`. Generated: `std::cout << ::tpyapp::__main__::run<int32_t>(::tpy::make_adapter<::tpystd::coro::Cancellable<int32_t>>(run())) << "\n";` -> g++ error: 'tpyapp::__main__' has not been declared. Renaming the user function to `runner` removes the error (isolated via /tmp/agents/bf/x2_await_opt.py).
- **Fix direction**: Resolve `asyncio.run` (and any module-attribute callee) strictly through the imported module's export table, never the current module's name table; separately, the `__main__` qname-to-namespace rendering disagrees with the entry module's actual C++ namespace and should go through module_to_cpp_namespace (context.py:194-203). Not borrow-form related; flagging for the name-resolution/gap-sweep reviewer.
- **Verifier adjustment**: Bug is real and reproduced (/tmp/agents/verif/f4_run.py): `async def run` + `asyncio.run(run())` emits `::tpyapp::__main__::run<int32_t>(::tpy::make_adapter<::tpystd::coro::Cancellable<int32_t>>(run()))`; control with the function renamed to `runner` emits the correct `::tpystd::asyncio::run<int32_t>(...)`. HOWEVER the finding is ALREADY TRACKED, verbatim, at BUGS.md:84: '[LOW small] A user-defined top-level function whose name collides with an imported stdlib function (e.g. def run(...) alon...
- *Independently found as*: "User async function named 'run' hijacks asyncio.run -- qualified module call emitted into the user namespace (C++ build failure)" (`parser`)

#### B120. User identifiers in the generated-temp namespace (__tmp_N, __slot_N, __obj_N, ...) collide with codegen names

- **Location**: tpyc/codegen_cpp/context.py:424-439, tpyc/codegen_cpp/context.py:405-413
- **Severity / category**: low / rejects-valid -- reproduced: yes -- found by `codegen-funcs-records`
- **Problem**: TempState/SlotState (and the iterator helpers __obj_N/__beg_N, __param_X) generate fixed-format names with no collision check against user identifiers. A user local literally named `__tmp_1` (legal Python at function scope; class scope would mangle it) collides with the first codegen temp in the same function, producing a C++ redeclaration error -- or, across scopes, a silent wrong-variable capture.
- **Evidence**: ```python
def main() -> None:
    __tmp_1 = 99
    add_one([5])
```
generates:
```cpp
int32_t __tmp_1 = 99;
std::vector<::tpy::BigInt> __tmp_1 = {5};   // redeclaration
```
- **Fix direction**: Either reserve the `__tpy_` prefix for all generated names (several families already use it; __tmp/__slot/__obj/__beg/__param do not), or escape user identifiers that match the generated-name grammar. Note escape_cpp_name's docstring exempts names 'starting with __tpy' as compiler-generated -- consolidating on that prefix makes the invariant real.

#### B121. with-as target emitted without escape_cpp_name

- **Location**: tpyc/codegen_cpp/statements.py:3213-3236
- **Severity / category**: low / rejects-valid -- reproduced: yes -- found by `codegen-stmt-match`
- **Problem**: Every other binding site escapes the Python name (escape_cpp_name) before emitting C++; _gen_with writes the raw `name` for all four as-target shapes. A with-as variable that collides with a C++ keyword produces invalid C++: the declaration emits the raw keyword while subsequent reads emit the escaped form, so they also disagree with each other.
- **Evidence**: Repro w2: `with Mgr() as template: print(template)` -> g++: "'template_' was not declared in this scope" (read site escaped to template_, declaration emitted raw `template`).
- **Fix direction**: Apply escape_cpp_name(name) at the four emit sites in _gen_with (keep the raw name for declared_vars/var_types bookkeeping, matching other paths).

### Theme: Function/record signature emission  (worst: medium, 4 findings)

#### B122. gen_params_with_protocols drops reassigned_params/addr_escapes_params -- body references undeclared __param_X

- **Location**: tpyc/codegen_cpp/functions.py:416-499, tpyc/codegen_cpp/functions.py:825-832, tpyc/codegen_cpp/functions.py:909-915, tpyc/codegen_cpp/functions.py:1009-1015
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-funcs-records`
- **Problem**: gen_params renames reassigned copy-for-reassign params (str, BigInt) to `__param_X` so the body can declare a mutable shadow, and disqualifies const for addr-escaping params. gen_params_with_protocols -- used whenever ANY param is a protocol (static or @dynamic) -- has no reassigned_params or addr_escapes_params plumbing, but body codegen unconditionally emits `std::string x = __param_x;` for the reassigned param. Any function mixing a protocol param with a reassigned str/BigInt param fails with a raw C++ 'not declared' error. The addr-escapes omission is the same shape: a protocol-having function whose other param's address escapes can be emitted const and then fail on the address-take. Classic sibling divergence between the two param emitters.
- **Evidence**: Repro:
```python
from typing import Sized
def announce(s: Sized, prefix: str) -> None:
    prefix = prefix + ": "
    print(prefix, len(s))
```
C++ error: `std::string prefix = __param_prefix;  error: '__param_prefix' was not declared in this scope`. Same failure with a @dynamic protocol param (k_dyn_reassign.py).
- **Fix direction**: Thread reassigned_params and addr_escapes_params through gen_params_with_protocols (its non-protocol fallback branch already calls decide_param_const, so the renaming branch from gen_params:370-373 is the missing piece), and pass rp/ae at the three call sites. Longer term, merge the two emitters so facts can't diverge again.
- **Verifier adjustment**: Bug is real and reproduced, but severity recalibrates from high to medium: the failure is a loud C++ compile error (rejects-valid), never silent wrong behavior, and has a trivial workaround (bind the param to a fresh local instead of reassigning). Code confirms the structural gap: gen_params_with_protocols (functions.py:416-422) takes no reassigned_params/addr_escapes_params and its decide_param_const call (486-494) omits both, while the call site at 825-832 passes rp/ae only to the gen_param...

#### B123. default None for Union-with-None param emits nullptr instead of monostate

- **Location**: tpyc/codegen_cpp/functions.py:153-160
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-funcs-records`
- **Problem**: default_to_cpp's TpyNoneLiteral branch handles OptionalType (std::nullopt vs nullptr) but falls through to `return "nullptr"` for a UnionType containing None. A param `v: A | B | None = None` lowers to `std::variant<std::monostate, A*, B*>` and `= nullptr` does not convert -- C++ compile error on the declaration itself. Exactly the Optional-vs-Union sibling gap named as the recurring bug class. (The same repro also shows a separate union-narrowing failure `v.y` after isinstance elimination -- outside these files, flag for the gap sweep.)
- **Evidence**: ```python
def f(v: A | B | None = None) -> int: ...
```
generates `::tpy::BigInt f(const std::variant<std::monostate, A*, B*> v = nullptr);` -> `error: could not convert 'nullptr' ... to 'const std::variant<std::monostate, A*, B*>'`. Call site `f()` fails identically.
- **Fix direction**: In the TpyNoneLiteral branch, detect a UnionType whose C++ form is a variant with a monostate member and emit `{}` (or `std::monostate{}`); keep nullptr only for pointer-repr Optional. Check Own[Union-with-None] for the same treatment.

#### B124. _gen_binary_operators ignores dunder const-ness and borrow-form return shape

- **Location**: tpyc/codegen_cpp/records.py:1567-1591
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-funcs-records`
- **Problem**: The friend operator wrapper hardcodes `const {Rec}& lhs`, `to_cpp_const_param` for the rhs, and `method.return_type.to_cpp()` for the return, while the real dunder is emitted via _resolve_return_type/_gen_method_overload with inferred const-ness and borrow-form (`const T&`) returns. Any dunder returning a borrowed reference type produces a friend with a mismatched return (`Acc&` wrapper vs `const Acc&` method) -- and since the friend body is compiled unconditionally for every non-template record, the whole program fails even if the operator is never used. A non-readonly dunder (one that mutates self or its param) breaks the same way via the const lhs/param. Where the types do line up but the return is a non-value type, to_cpp() returns by value, so the wrapper would silently copy a borrowed return. _gen_unary_operators and _gen_record_ostream/_gen_size_method share the const-receiver assumption (mitigated by readonly inference).
- **Evidence**: ```python
def __add__(self, other: 'Acc') -> 'Acc':
    return self
```
generates:
```cpp
const Acc& __add__(const Acc& other) const;
friend Acc& operator+(const Acc& lhs, const Acc& other) {
    return lhs.__add__(other);   // error: binding 'Acc&' to 'const Acc' discards qualifiers
}
```
g++: hard error compiling the struct.
- **Fix direction**: Derive the friend's lhs const-ness from method.is_readonly, the rhs spelling from the method's actual param decision, and the return from _resolve_return_type(const=method.is_readonly) instead of bare to_cpp(); or emit the operator only when the shapes are compatible.

#### B125. _gen_call_operator forces const params, breaking __call__ that mutates a param

- **Location**: tpyc/codegen_cpp/records.py:1593-1610
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `codegen-funcs-records`
- **Problem**: operator() always renders every param via to_cpp_const_param and forwards to __call__, ignoring mutated_params. When __call__ mutates a reference-type param (emitted as non-const `T&`), the operator() body passes a const ref to a T& param -- ill-formed, and compiled unconditionally as a member of a non-template struct, so the class cannot be used at all. operator() also drops parameter defaults, so C++-side calls through the call operator can't use them (TPy call sites route to __call__ directly, masking it).
- **Evidence**: ```python
class Filler:
    def __call__(self, xs: list[int]) -> None: xs.append(self.v)
```
generates:
```cpp
void __call__(std::vector<::tpy::BigInt>& xs) const;
void operator()(const std::vector<::tpy::BigInt>& xs) const {
    return __call__(xs);   // const& passed to vector& -- ill-formed
}
```
- **Fix direction**: Use the same gen_params/mutated_params machinery as gen_method_def for the operator() signature (and emit defaults), mirroring how the subscript operators delegate to the real method shapes.

### Theme: Parser fidelity: dropped or mis-lowered syntax  (worst: high, 6 findings)

#### B126. Positional-only parameters (def f(a, /, b)) silently dropped from function signatures

- **Location**: tpyc/parse/parser.py:2567, tpyc/parse/parser.py:2221, tpyc/parse/parser.py:3929
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `parser`
- **Problem**: Both _parse_function and _parse_method build params exclusively from node.args.args; ast.arguments.posonlyargs is never read (grep confirms the only parser reference is the lambda rejection at parser.py:3807). Every parameter before a '/' separator vanishes from the compiled signature with no diagnostic at the def site. Consequences: (1) defaults are misaligned because ast defaults right-align over posonlyargs+args combined but _parse_param_defaults aligns them against args only, so a later parameter silently inherits the wrong default and calls bind to the wrong slot -- observed wrong runtime output with zero diagnostics; (2) when the body uses the dropped parameter the user gets a misleading 'Undefined variable' error pointing into the body. Lambdas explicitly reject posonlyargs, so def/method are the inconsistent siblings. Methods have the same hole (the args loop at parser.py:2221 also iterates node.args.args only).
- **Evidence**: Repro A (silent wrong output): def f(a: int, /, b: int = 5) -> int: return b; print(f(7)) -- CPython prints 5 (a=7, b=5); `uv run tpy /tmp/agents/posonly2.py` compiles cleanly and prints 7 (f lowered to single param b with default 5; the call binds 7 to b). Repro B (misleading diagnostic): def f(a: int, /, b: int) -> int: return a + b -> 'posonly.py:2: error: Undefined variable: 'a'' instead of any signature-level message.
- **Fix direction**: Either prepend node.args.posonlyargs to the params walk (treating them as ordinary positional params, since TPy has no posonly enforcement yet) and fix _parse_param_defaults' num_ast_args to count posonlyargs+args, or reject '/' with a clear 'positional-only parameters are not supported' ParseError as the lambda path does. Check the sibling paths together: free functions, methods, @overload stubs, protocol method signatures, and @builtin_function stubs all iterate node.args.args.

#### B127. Annotated attribute assignment in __init__ (self.x: T = value) rejected as 'Invalid assignment target'

- **Location**: tpyc/parse/parser.py:3147
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `hunt-silent-copy`
- **Problem**: The standard CPython idiom `self.tags: list[Int32] = []` inside __init__ is a ParseError ('Invalid assignment target'). TPy requires class-level annotations + un-annotated assignment instead. This is arguably the single most common way Python code declares instance attribute types (CPython creates instance attributes only via __init__ assignment, and CLAUDE.md itself instructs adding __init__ methods for CPython parity), so rejecting the annotated form forces a syntax split between TPy and idiomatic Python, and the diagnostic gives no hint about the class-level-annotation workaround.
- **Evidence**: Repro: class P:\n    def __init__(self):\n        self.tags: list[Int32] = []  -> aliasA.py:5: error: Invalid assignment target. Parser: `raise ParseError("Invalid assignment target", node)` for annotated non-Name targets (parser.py:3147).
- **Fix direction**: Accept TpyAnnAssign with an attribute target on self inside __init__, treating it as the field's declaration (equivalent to the class-level annotation) or validating it against an existing one; at minimum improve the diagnostic to point at the class-level annotation form.
- *Independently found as*: "Annotated attribute assignment (self.x: int = v) rejected with generic 'Invalid assignment target'" (`parser`)

#### B128. await in an assert message runs unconditionally -- side effect executes even when the assertion passes

- **Location**: tpyc/parse/desugar_suspensions.py:124-141
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `parser`
- **Problem**: CPython evaluates an assert's message expression only when the condition is false. desugar_suspensions treats every TpyExpr field of a statement uniformly as 'evaluated once before the statement' and leaves unconditional awaits in place for the codegen lifter, which hoists them before the statement. TpyAssert.message is a conditionally-evaluated position (like a ternary branch), so the hoisted await -- and any side effects of the awaited coroutine -- run even on the passing path. Silent behavior divergence, no diagnostic.
- **Evidence**: /tmp/agents/assertmsg.py: async def go(): x = 1; assert x == 1, await msg() where msg() prints 'side effect'. CPython prints only 'done'; `uv run tpy /tmp/agents/assertmsg.py` prints 'side effect' then 'done'.
- **Fix direction**: Treat TpyAssert.message as a conditional position in the desugarer: rewrite `assert C, await M` into `if not C: __m = await M; <raise AssertionError(__m)>` (or simply reject awaits in assert messages). Same conditional-position audit should cover any other lazily-evaluated expr slots (match guards above; `and`/`or`/ternary/while are already handled).

#### B129. NameError: 'ref' is not defined in TypeResolver._resolve_qualified_cross_module (generic-alias arm)

- **Location**: tpyc/parse/type_resolver.py:986
- **Severity / category**: low / crash -- reproduced: no (code-read evidence) -- found by `parser`
- **Problem**: In _resolve_qualified_cross_module, the cross-module bare-generic-alias rejection constructs `SemanticError(..., loc=ref.loc)` but `ref` is not a parameter of this method (the function takes source_module/attr/loc). Any module that does `from pkg import submod` and then annotates with a bare `submod.GenericAlias` (a generic `type Alias[T] = ...` referenced without type args) crashes the compiler with an internal NameError instead of producing the intended 'requires type arguments' diagnostic. The two sibling arms in the same function (protocols, line 992-997) correctly use `loc=loc`, making this a copy-edit slip on the alias arm.
- **Evidence**: type_resolver.py:978-987: `if alias_info.type_params: ... raise SemanticError(f"Generic type alias '{attr}' requires type arguments: ...", loc=ref.loc)` -- no `ref` in scope (method signature at line 954: `def _resolve_qualified_cross_module(self, source_module, attr, *, loc=None)`). Python will raise NameError before SemanticError is constructed.
- **Fix direction**: Replace `loc=ref.loc` with `loc=loc`. Add a test covering the from-pkg-import-submod + bare-generic-alias annotation path (the same-module and short-name-import arms already raise correctly at lines 925-936).
- **Verifier adjustment**: The textual defect is real: tpyc/parse/type_resolver.py:986 has 'loc=ref.loc' but the method signature (line 954-957) takes only (source_module, attr, *, loc); the sibling protocol arm at 996 correctly uses 'loc=loc'. However the claimed impact ('any module that does from pkg import submod and then annotates with a bare submod.GenericAlias crashes the compiler') is wrong: the method is dead code from its only call site. At type_resolver.py:444-455, the cross-module call is reached only when _...

#### B130. raise E(...) from cause: the 'from' clause is silently discarded

- **Location**: tpyc/parse/parser.py:3297-3315
- **Severity / category**: low / miscompile -- reproduced: yes -- found by `parser`
- **Problem**: _parse_raise reads node.exc but never node.cause; TpyRaise (nodes.py:930-943) has no cause field. `raise RuntimeError("outer") from e` parses and compiles to a plain `throw ::tpy::RuntimeError("outer")` -- the explicit-chaining clause is accepted-then-ignored with no diagnostic. Since TPy's exception model doesn't expose __cause__ the runtime impact is limited (parity divergence is mostly invisible), but a construct that parses and silently changes meaning violates the 'no silent divergence' bar; `raise X from None` (suppressing implicit context) is likewise silently ignored.
- **Evidence**: /tmp/agents/raisefrom.py: `raise RuntimeError("outer") from e` -> generated main.cpp contains only `throw ::tpy::RuntimeError("outer");` (lines 266-268 region of dump); no warning in diagnostics.
- **Fix direction**: Either warn ('exception chaining (raise ... from ...) is not modeled; the cause is dropped') or reject until chaining is supported. One-line check in _parse_raise: `if node.cause is not None: ...`.

#### B131. Multi-target assignment desugars with rightmost-name anchor, inverting CPython's left-to-right target order

- **Location**: tpyc/parse/parser.py:3385-3423
- **Severity / category**: low / miscompile -- reproduced: yes -- found by `parser`
- **Problem**: _parse_multi_assign anchors on the RIGHTMOST Name target: `a[k()] = b = expr` lowers to `b = expr; a[k()] = b`, whereas CPython assigns targets left-to-right (a[k()] first, then b). The value-once property and reference aliasing are preserved (verified: `a = b = [1,2]; b.append(3)` aliases correctly in TPy), but side-effect ordering between target subexpressions and earlier assignments diverges, and an exception thrown by an earlier target's evaluation leaves a different set of bindings done. Very narrow observable surface; no diagnostic.
- **Evidence**: parser.py:3399-3403: 'Find rightmost Name target to use as anchor' then loop assigns remaining targets after the anchor decl. CPython reference semantics: targets evaluated/assigned left to right (language reference 7.2). Aliasing repro /tmp/agents/multiassign.py prints [1,2,3] twice (correct).
- **Fix direction**: Anchor on a synthetic temp (or the leftmost Name) and assign targets strictly left-to-right; the temp-based form already exists for the no-Name case at parser.py:3409-3412, so using it unconditionally is the smallest correct change.

### Theme: Orchestration, REPL, CLI  (worst: high, 5 findings)

#### B132. REPL is completely broken: include-path overrides read outside compiler context

- **Location**: tpyc/repl.py:442, tpyc/codegen_cpp/context.py:166-176
- **Severity / category**: high / crash -- reproduced: yes -- found by `orchestration-cli`
- **Problem**: In _try_compile_and_run, repl.py calls get_include_path(mod.name) AFTER compiler.compile() returned, i.e. outside any activate_compiler() context. get_include_path returns None when no compiler is active, so every '# tpy: include()' override (all private stdlib submodules: tpy._core._types -> tpystd/tpy/_types.hpp, tpy._typing -> tpystd/typing/_typing.hpp, ...) is lost and the generated .hpp/.cpp files are written under the dotted-name fallback path. The generated #include lines (emitted inside the context by generate_code_to_strings) still reference the override paths, so the very first C++ compile of any REPL session fails. Since 'from tpy import *' is auto-injected, the default `tpy` REPL (CompileBackend, the auto-detected backend) cannot execute even `print(1+2)`. Regression from the per-compilation-state ContextVar migration: the get_include_path docstring only considered BuildLayout path-shape tests as out-of-context callers and missed this one.
- **Evidence**: printf 'print(1+2)\nexit\n' | uv run tpyc --repl ->
>>> C++ compilation failed:
/tmp/tpyc_repl_bhsrde3o/tpy/_typing.cpp:2:10: fatal error: tpystd/typing/_typing.hpp: No such file or directory

repl.py:442: include_path = get_include_path(mod.name)  # returns None outside activate_compiler
context.py:173-176: compiler = get_current_compiler(); if compiler is None: return None
- **Fix direction**: Wrap the per-module codegen+file-write loop in repl.py (lines ~433-465) in `with activate_compiler(compiler):`, or read compiler.include_path_map directly instead of the context helper. Add a non-interactive REPL smoke test (pipe one input through REPLSession) -- nothing in CI currently executes the CompileBackend path end to end. Sibling check for the gap-sweep: audit all other out-of-context callers of context.py helpers (qualified_cpp_name, module_to_include_path) for the same silent-None failure mode.

#### B133. Compiler crash (AssertionError) in detect_type_cycles on valid recursive-union code: duplicate-cycle dedup leaves node GRAY

- **Location**: tpyc/cycle_detection.py:340-343, tpyc/cycle_detection.py:331
- **Severity / category**: high / crash -- reproduced: yes -- found by `orchestration-cli`
- **Problem**: In detect_type_cycles' DFS, when a back-edge reconstructs a structural cycle already in seen_cycles, the code executes `return` instead of continuing the edge loop. This (a) skips the node's remaining edges and (b) skips `color[u] = BLACK`, leaving u GRAY forever. Any later DFS root with an edge into the stuck-GRAY node treats it as a back-edge and reconstructs a 'cycle' by walking parent_edge -- which hits a root with parent_edge=None and trips `assert pe is not None` (or, in other shapes, fabricates a bogus cycle that can surface as a spurious 'infinite-size cycle' error). Trigger: a union alias that sorts alphabetically before a record holding TWO indirected references to it (the canonical BinOp left/right shape), plus any third record referencing that record. Crashes with a bare 'Internal error:' and no diagnostic.
- **Evidence**: /tmp/agents/cycle_bug2.py: `type AExpr = Lit | BinOp`; `class BinOp: left: Box[AExpr]; right: Box[AExpr]`; `class Zuser: b: BinOp`.
uv run tpy --dump-code -> 'Internal error:' ; with -v:
  File tpyc/cycle_detection.py, line 356, in detect_type_cycles -> line 331, in dfs
    assert pe is not None
AssertionError
Buggy code (line 340-343):
  key = frozenset(path)
  if key in seen_cycles:
      return        # <- aborts dfs(u) without marking BLACK
  seen_cycles.add(key)
- **Fix direction**: Replace the `return` with flow that skips only the recording (e.g. `if key not in seen_cycles: seen_cycles.add(key); cycles.append(...)`) so the edge loop completes and color[u]=BLACK always runs. While there: dfs is recursive over the type graph (compiler.py's Tarjan was made iterative 'for safety on deep import graphs'); deep record chains can hit Python's recursion limit -- consider the same iterative treatment.

#### B134. REPL session crash: _try_compile_and_run returns bare string on diagnostic-level errors

- **Location**: tpyc/repl.py:429-430, tpyc/repl.py:325
- **Severity / category**: medium / crash -- reproduced: yes -- found by `orchestration-cli`
- **Problem**: _try_compile_and_run is declared -> tuple[bool, str] and every caller unpacks `success, output = ...`, but the has_errors branch does `return diag_output.rstrip()` -- a bare string. Unpacking a multi-char string raises ValueError('too many values to unpack'), which propagates through _process_input into run()'s loop (which catches only EOFError/KeyboardInterrupt) and kills the whole REPL session, losing accumulated state. Reachable by any input whose error is recorded via ctx.emit_error (non-raising diagnostics): comparison on a class without the dunder, nonlocal in escaping closures, str-param capture in escaping closures.
- **Evidence**: Repro (REPLSession driven directly): input
  class P: ... 
  def cmp(a: P, b: P) -> bool: return a < b
_try_compile_and_run returned: str "repl:8: error: Comparison '<' on 'P': no '__lt__' method defined"
Then `success, output = s._try_compile_and_run(src)` -> ValueError('too many values to unpack (expected 2)')
- **Fix direction**: return (False, diag_output) at repl.py:430. Related robustness in the same function: the auto-print re-compile at repl.py:409-419 catches only (ParseError, SyntaxError, SemanticError) -- a CompileError or internal exception there also propagates and kills the session; the run() loop should catch Exception around _process_input as a last-resort guard.

#### B135. Cycle peers analyzed first see stale (pre-Phase-2) readonly/mutation facts: order-dependent rejects-valid

- **Location**: tpyc/compiler.py:1289-1318 (analysis ordering), tpyc/compiler.py:1308-1314 (borrow-only deferral)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `orchestration-cli`
- **Problem**: Within an import SCC, members are body-analyzed in alphabetical order, and each module's run_phase2_fixpoint (which infers method is_readonly from mutation facts) runs inside its own _analyze_bodies. Only *borrow* checks are deferred workspace-wide (finalize_borrow_checks loop); readonly-method-call legality is checked eagerly during the consumer's body sema. So a cycle member analyzed before its peer sees the peer's methods with the un-inferred default (not readonly) and rejects a call to an obviously-readonly method on a readonly reference -- while the byte-identical program with module names swapped (definer alphabetically first) compiles. Conservative direction (rejects-valid, never unsound), workaround is an explicit @readonly decorator. This is the cross-module manifestation of the known intra-module Phase-1-reads-Phase-2-fact conservatism (BUGS.md lines 334, 382); BUGS.md does not track the cross-module/order-dependent variant.
- **Evidence**: cyc1 (consumer 'a' < definer 'z', cycle a<->z): def use(t: readonly[z.Thing]) -> int: return t.get() where Thing.get only reads self.x ->
  a.py:5: error: Cannot call non-readonly method 'get' on readonly reference (exit 1)
cyc2 (identical code, consumer renamed 'zz' so definer 'a' analyzed first) -> exit 0.
cyc1 + explicit @readonly on get -> exit 0 (workaround confirmed).
- **Fix direction**: Defer readonly-method-call checks on cross-module (at minimum cross-cycle-peer) receivers the same way borrow checks are deferred -- queue them and resolve in the workspace-wide finalize loop after every module's Phase-2 fixpoint. Gap-sweep note: any other eager consumer of Phase-2 facts (auto-move decisions, Send/Sync frame traits, mutation-based overload choices) has the same cross-cycle staleness exposure; I only verified the readonly-method path.

#### B136. tpy -c / stdin compilation leaks a temp directory per invocation; PCH cache key ignores compiler identity

- **Location**: tpyc/cli.py:480, tpyc/compiler.py:392-429
- **Severity / category**: low / slop -- reproduced: yes -- found by `orchestration-cli`
- **Problem**: (a) For -c/stdin input the CLI does tempfile.mkdtemp(prefix='tpyc_') and never removes it, so every `tpy -c "..."` leaves a build tree (objects + binary) in /tmp. (b) get_or_build_pch's staleness check compares only runtime-header mtimes against the .gch; the compiler identity, std, and warn/extra flags are not part of the key. Switching --cxx between runs against the same output dir reuses a foreign .gch -- verified harmless-but-wasteful for gcc->clang (clang warns '-Wignored-gch' and reparses headers every TU, silently losing the PCH benefit) and similarly for GCC version bumps (GCC silently ignores incompatible PCH). The REPL's _get_pch_cache_dir (repl_backends.py:276-283) already does this correctly by hashing compiler_name+std+flags into the cache key.
- **Evidence**: cli.py:480: temp_dir = tempfile.mkdtemp(prefix='tpyc_')  # no cleanup/atexit
compiler.py:408-414: staleness = any(h.stat().st_mtime > pch_mtime for h in runtime_tpy.glob('**/*.hpp'))  # no compiler/flags in key
Verified: g++-built hdr.hpp.gch + clang++-18 -include hdr.hpp -> 'warning: precompiled header ... ignored because it is not a clang PCH file' (compiles, no PCH).
- **Fix direction**: (a) shutil.rmtree(temp_dir) on exit unless the user passed -o. (b) Reuse the REPL backend's keying scheme (hash of compiler_name:std:flags) for the CLI PCH dir, or store a sidecar key file and rebuild on mismatch.

### Theme: Macro system  (worst: medium, 4 findings)

#### B137. macro_deps() pending-global is stolen by a nested macro-module import

- **Location**: tpyc/macro_api.py:207-242, tpyc/macro_loader.py:474-488
- **Severity / category**: medium / design -- reproduced: yes -- found by `macros`
- **Problem**: macro_deps() communicates with MacroRegistry.load_module via the module-level global macro_api._pending_macro_deps. If macro module A calls macro_deps(...) and THEN imports another macro module B (a legal Python ordering; the restricted __import__ triggers a nested load_module(B) re-entrantly), B's load_module epilogue reads the still-pending global and attributes A's deps to B, resetting the global to None -- so A ends up with NO deps and B with deps it never declared. A's macro expansions that reference dep modules then fail with 'Undefined variable', and modules using B's macros silently get extra dep modules bound. Order-dependent, silent, and also violates the CLAUDE.md rule that per-compilation state must live on the Compiler, not module-level globals (this global additionally leaks across Compiler instances if a module load raises between set and read in some paths).
- **Evidence**: Repro /tmp/agents/audit/deps: macro_a.py = `macro_deps("dep_infra")` followed by `import macro_b`; @call_macro shout() expands to dep_infra.emit(arg). Result: `main.py:3: error: Undefined variable: 'dep_infra'`. Moving `import macro_b` BEFORE macro_deps() makes the identical program compile (generated C++ contains `::tpyapp::dep_infra::emit("hello")`). Mechanism: macro_loader.py:486-488 runs after B's exec_module while A's deps are still pending: `if _macro_api._pending_macro_deps is not None: self._macro_deps[module_name] = ...` with module_name == B.
- **Fix direction**: Make dep registration re-entrancy-safe: save/restore _pending_macro_deps around the nested exec_module in load_module (push None before spec.loader.exec_module, pop after reading), or better, replace the global handshake with an explicit registry-scoped channel (e.g. a contextvar or a per-load slot on MacroRegistry keyed by the module being executed).

#### B138. Builder-trace symbol unusable after trace close, contradicting documented contract

- **Location**: tpyc/sema/builder_trace.py:222-228, 255-304, 383, 445, 472-484 (vs docstring 231-242)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `macros`
- **Problem**: _reject_active_reassignment's docstring states 'Closed names are not rejected -- the trace has finished, the name is free to take on any other meaning', and the terminal handler indeed removes the symbol from _tracked/_open_traces. But the post-pass leak validator (_validate_no_tracked_leaks) checks against _all_tracked_names, which is never pruned on close. So rebinding a closed builder name succeeds, but any subsequent USE of the rebound name is rejected with the unrelated error 'may only appear as the receiver of a registered @builder_method call'. Valid Python (runs fine under CPython argparse) is rejected, and the internal contract is self-contradictory.
- **Evidence**: Repro /tmp/agents/audit/builder_reuse.py:
```python
parser = ArgumentParser()
parser.add_argument("name")
args = parser.parse_args(["alice"])
print(args.name)
parser = 5      # accepted (trace closed)
print(parser)   # rejected
```
Observed: `builder_reuse.py:11: error: builder-trace symbol 'parser' may only appear as the receiver of a registered @builder_method call` -- fired on the use of the legitimately rebound value.
- **Fix direction**: Track closed names separately: on terminal close, move the closed set out of the validation universe from the close point onward (the validator is a flat post-pass, so either record the statement index of close and only validate earlier statements, or validate during the forward walk instead of a post-pass). Alternatively prune _all_tracked_names on close and only validate names still open at end-of-body plus uses that occurred while the trace was open.

#### B139. Builder ctor inside control flow / nested def silently unexpanded -> misleading 'Unknown function' error

- **Location**: tpyc/sema/builder_trace.py:207-229, 306-336
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `macros`
- **Problem**: expand() walks only the top-level statement list; _process_stmt never recurses into sub_bodies or TpyNestedDef bodies. A builder constructor written inside an if/for/while/with/try (or a nested def) is therefore never seen by the expander, and the un-expanded macro-class call falls through to ordinary sema, which reports 'Unknown function ArgumentParser in module argparse' -- a false statement (the name exists; it is a builder macro the trace pass chose not to handle). The Phase-A docstring acknowledges control-flow rules are pending (Phase B), but the failure mode today is a misleading diagnostic rather than a clear 'builder macros must be used in straight-line code' error. Note the asymmetry: method calls ON a tracked symbol inside control flow DO get a precise dedicated error (in_control_flow branch in _validate_expr); the constructor case gets nothing.
- **Evidence**: Repro /tmp/agents/audit/builder_if.py:
```python
def main(flag: bool) -> Int32:
    if flag:
        parser = ArgumentParser()
        parser.add_argument("name")
        ...
```
Observed: `builder_if.py:7: error: Unknown function 'ArgumentParser' in module 'argparse'` (CPython runs this fine).
- **Fix direction**: Until Phase B lands: detect builder-macro constructor calls in nested bodies during expand() (cheap recursive scan for resolved_import hitting get_builder_macro) and raise the dedicated 'builder-trace constructor may not appear inside a control-flow block' error, mirroring the existing tracked-symbol-in-control-flow diagnostic. Gap-sweep note: same misleading error fires for builders constructed in lambda/comprehension and in nested defs.

#### B140. FunctionMacroContext.replace_expr blind to exprs under non-Stmt/Expr wrapper nodes (except handlers, match cases, comprehension generators, f-string parts)

- **Location**: tpyc/macro_api.py:601-656, 769-773
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `macros`
- **Problem**: _replace_node_in recurses only into field values that are TpyExpr/TpyStmt instances (directly or inside list/dict/tuple containers). But several AST container nodes are plain dataclasses that subclass NEITHER: TpyExceptHandler (TpyTry.handlers), TpyMatchCase (TpyMatch.cases), TpyComprehensionGenerator (comprehensions' .generator -- holds .iterable and .conditions exprs), TpyFStringValue (TpyFString.parts). An expression located under any of these is unreachable, so replace_expr errors 'node not found in function body' even though the node IS in the body. annotate_local is unaffected (it uses sub_bodies(), which does cover handler/case bodies), making the asymmetry surprising to macro authors. Affects @function_macro authors (e.g. the documented local-deduction style macros) walking realistic bodies containing try/except, match, comprehensions, or f-strings.
- **Evidence**: Empirical check (/tmp/agents/audit/replcheck.py) using the real FragmentParser + _replace_in_body:
- target = the `compute()` call inside an except-handler body -> `_replace_in_body(...)` returned False;
- target = the iterable `source()` of a list comprehension's generator -> returned False.
Node-class confirmation: parse/nodes.py:947 (TpyExceptHandler), 1095 (TpyMatchCase), 488 (TpyComprehensionGenerator), 198 (TpyFStringValue) -- none subclass TpyExpr/TpyStmt.
- **Fix direction**: Recurse into a known set of wrapper dataclasses (TpyExceptHandler, TpyMatchCase, TpyComprehensionGenerator, TpyFStringValue, TpyWithItem, pattern nodes) in _replace_node_in, or generically: recurse into any dataclass instance from the tpyc.parse.nodes module rather than gating on (TpyExpr, TpyStmt). Gap-sweep note: any other generic dataclass-reflection walker gated the same way (check parse/resolve_refs and codegen walkers) shares the blind spot.


## 5. Round-2 gap-sweep findings (verified)

The gap sweep was seeded with round-1 results and targeted sibling constructs, declared coverage holes, and four fresh-eyes areas (exceptions/`with`/`@error_return`, strings/bytes/f-strings, enums/unions/Optional/match, cross-feature interactions in the two largest files). Re-discoveries of round-1 findings are listed at the end, not repeated.

#### G1. `break` inside a switch-lowered match arm exits the switch, not the enclosing loop

- **Location**: tpyc/codegen_cpp/statements.py:3615-3645 (_make_break_continue emits plain 'break;'), tpyc/codegen_cpp/match.py:842 (_emit_switch_groups) and all switch-based paths (_gen_match_switch_union/enum/primitive/str)
- **Severity / category**: critical / miscompile -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: Python `break` inside a match arm inside a for/while loop must exit the loop. TPy lowers enum/union/int/str matches to C++ `switch`, and a user `break` in the arm body is emitted as a bare `break;` which C++ binds to the switch. The loop silently continues. Reproduced at runtime with divergent output. Note the asymmetry: the guarded match paths (standalone if + goto), record/if-elif paths, and loops with an `else:` clause (break -> goto else-label) are NOT affected -- only the common unguarded switch paths in plain loops, which makes the bug feel nondeterministic to users. The design doc even avoided do/while(false) for guards 'which would capture break/continue from user code' but missed that the switch itself captures break.
- **Evidence**: Repro: for c in [Color.Red, Color.Green, Color.Blue]: match c: case Color.Green: break; case _: print(c.name). Generated: `switch (__match_subject) { case Color::Green: { break; break; } ... }` inside the for loop. TPy output: 'Red\nBlue\ndone'; CPython output: 'Red\ndone'.
- **Fix direction**: When emitting a user break whose innermost loop contains an intervening match-switch, emit a goto past the loop (reuse the loop-else label machinery, which already proves the goto approach works) or lower such matches to the if/elif form. Needs a 'inside match-switch' depth marker in CodeGenContext consulted by _make_break_continue. Sibling check for the gap-sweep: `break` inside the optimized-Optional inner switch and inside the 5+-string switch dispatch have the same hole.

#### G2. Match arm bindings are unchecked `auto&` borrows into subject storage -- arm-body mutation of the subject (variant reassign, list append) leaves dangling references

- **Location**: tpyc/codegen_cpp/match.py:171 (auto& __match_subject = field/element expr), tpyc/codegen_cpp/match.py:664-668 (auto& __case_N = std::get<N>, field bindings), tpyc/codegen_cpp/match.py:1001 (_emit_binding auto&)
- **Severity / category**: critical / ub -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: When the match subject is a union FIELD (storage-form value variant) or a container element, arm bindings (`case Dog(name=n):`, `case Dog() as d:`) are `auto&` references into that storage. Reassigning the field in the arm body destroys the variant alternative in place; appending to the source list reallocates the vector. Either way the live binding (and __match_subject itself) dangles and the subsequent read is C++ UB. CPython is safe in both shapes (the binding holds the old object). No sema warning or borrow-checker diagnostic fires. Note pointer-variant LOCAL subjects are safe (slot model: reassignment fills a fresh __slot_N), which makes the field/element hole easy to miss. Guards run after bindings are emitted, so a subject-mutating guard hits the same class.
- **Evidence**: Repro A: class Holder: pet: Dog | Cat; match h.pet: case Dog(name=n): h.pet = Cat(9); print(n). Generated: `auto& __case_0 = std::get<1>(__match_subject); auto& n = __case_0.name; h.pet = Cat(9); std::cout << n;` -- variant emplace destroys the Dog, n reads a destroyed std::string. Repro B: xs: list[Dog | Cat]; match xs[0]: case Dog(name=n): xs.append(Cat(9)); print(n). Generated: `auto& __match_subject = ::tpy::__getitem__(xs, 0); ... xs.push_back(Cat(9)); std::cout << n;` -- push_back reallocates, n dangles. Both compile with zero diagnostics.

#### G3. Finally body (and with __exit__) runs BEFORE the return expression is evaluated

- **Location**: tpyc/codegen_cpp/statements.py:3503-3519, tpyc/codegen_cpp/statements.py:3455-3492, docs/EXCEPTION_DESIGN.md:588-599
- **Severity / category**: critical / miscompile -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: _make_return emits the enclosing finally chain inline and only then emits `return <expr>;`, so the return expression is evaluated AFTER the finally body. Python evaluates the return value first, then runs finally. Any finally (or with-statement __exit__, which uses the same FinallyContext frames) that mutates state read by the return expression silently returns the wrong value. Classic idioms break: `try: return self.conn finally: self.conn = None`, `with lock: return shared.pop()` (the pop runs after the lock is released -- also a race in threaded code). Note the design doc itself specifies the correct shape (`__retval = X; goto __finally;`) -- the implementation diverged from the documented design. Same mechanism is used by _make_async_return / _make_generator_resumable_return for frames without CFG-based finally, so async/generator sync-finally paths share the bug (CFG-based finally with pending_return_slot saves the value first and is correct -- an inconsistency between siblings).
- **Evidence**: Repro: def f() -> int: x = 1; try: return x finally: x = 2. Generated C++: `try { x = 2; return ::tpy::BigInt(x); } catch (...) { x = 2; throw; }`. Observed output: 2 (CPython: 1). With-variant repro: `with lk: return lk.held` where __exit__ sets held=False generates `__ctx_1.__exit__({}, {}, {}); return lk.held;` -- observed output: False (CPython: True). Both built and run.
- **Fix direction**: Evaluate the return expression into a temporary (the doc's `__retval = X` shape) before emitting the finally chain, then return the temp. Must cover sync returns, _gen_propagate_check's make_unexpected returns, break/continue are unaffected (no value). Check the same ordering for async returns through _make_async_return when no pending slot is active, and for _gen_raise's return-tier `raise E(args)` (args are evaluated in the emitted return after the chain too -- same hazard if finally mutates state read by the args).

#### G4. finally body analyzed with try-body narrowing facts: null-deref UB emitted inside the p==nullptr branch

- **Location**: tpyc/sema/statements.py:1743-1753, tpyc/sema/statements.py:1973-1978
- **Severity / category**: critical / ub -- reproduced: yes -- found by `gap-siblings`
- **Problem**: Sibling of the round-1 'except handlers analyzed from pre-try state' finding, on the finally axis. _analyze_try_finally_only (and the throw-tier path at 1973-1978) analyzes the finally body sequentially from the END-of-try-body state, so Optional narrowing established inside the try (e.g. by an early-return None guard) is assumed to hold in finally. But finally also runs (a) when an exception escapes BEFORE the narrowing point and (b) on the early-return path where the variable is provably None. Sema accepts the deref with no diagnostic and codegen emits an unguarded pointer deref -- including, in the repro, literally inside the `if (p == nullptr)` block before the early return, a guaranteed null deref. CPython raises AttributeError; TPy is silent UB. try/finally cleanup touching an Optional is mainstream code.
- **Evidence**: Repro /tmp/agents/sib/t9.py: `p: P | None = P(); if c: p = None` then `try: might_raise(c); if p is None: return; print(p.v)  finally: print(p.v)`. Compiles with zero diagnostics. Generated C++: `try { might_raise(c); if ((p == nullptr)) { std::cout << p->v << "\n"; return; } std::cout << p->v << "\n"; } catch (...) { std::cout << p->v << "\n"; throw; }` -- the inlined finally dereferences `p->v` inside the `p == nullptr` branch and in the catch-all where p may be nullptr (main(true) makes might_raise throw with p=nullptr). Code: statements.py:1746-1752 runs `analyze_stmt` on try_body then immediately on finally_body with no state merge/havoc; the throw tier (1973-1978) analyzes finally from the merged success+handler EXIT states, equally ignoring mid-body exception points.
- **Fix direction**: Analyze the finally body from a conservative state: intersect the pre-try state with every statement-boundary state of the try body (or simply havoc narrowing/value-range/non-null facts for variables written or narrowed inside the try, mirroring whatever fix lands for the round-1 except-handler hole). Sibling not checked: the resumable (CFG-based) finally path in resumable_cfg.py likely inherits the same sema facts -- gap-sweep should trace it.

#### G5. Locals initialized from StrView/BytesView-returning expressions bypass all view-lifetime tracking -> dangling string_view (UAF), runtime-confirmed

- **Location**: tpyc/typesys.py:4096-4118, tpyc/sema/local_deduction.py:904-908, tpyc/sema/statements.py:2703-2714
- **Severity / category**: critical / ub -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: The PendingStrType/PendingBytesType safety net (promote view to owned when the source is a temporary or is later mutated) only engages when the init expression's type is the OWNED family type: view_family_for_type maps only 'builtins.str'/'builtins.bytes' qnames (_VIEW_OWNED_QNAME_TO_FAMILY), so any local whose init type is already StrView/BytesView -- slices (s[i:j]), strip/lstrip/rstrip, removeprefix/removesuffix, and their bytes siblings -- is typed as a raw std::string_view/std::span local with NO ViewVarInfo, no source-borrow registration, and no source_mutated fall-back. Additionally is_view_compatible_source (local_deduction.py:904-908) blesses any TpyCall/TpyMethodCall returning a view type unconditionally, ignoring receiver lifetime. Three UAF shapes all compile silently: (a) view of a destroyed temporary: s = make().strip() / t = '  pad  '.upper().strip(); (b) view of an lvalue mutated afterwards: v = a.strip(); a += ... (std::string reallocates, view dangles); (c) slice of an lvalue mutated afterwards: v = a[2:20]; a += .... The bytes sibling (v = b[1:4]; b = give()) emits the same dangling span. The documented safety mechanism (STRING_HANDLING.md 'Alias source tracking ... prevents dangling string_view') simply never fires for these inits.
- **Evidence**: Repro 1: def make() -> str: return '...long...'; def main(): s = make().strip(); print(s) -> emits `std::string_view s = ::tpy::str_strip(make());` ; built binary prints garbage: `d\xef... is long enough to avoid sso buffers`. Repro 2: a = make(); v = a.strip(); a += '...' -> emits `std::string_view v = ::tpy::str_strip(a); a += ...;` ; binary prints garbage. Repro 3: a = make(); v = a[2:20]; a += '...' -> emits `std::string_view v = ::tpy::str_slice(a, ::tpy::BasicSlice{2, 20}); a += ...;` ; binary prints garbage. Bytes sibling: b = give(); v = b[1:4]; b = give() -> `std::span<const uint8_t> v = ::tpy::bytes_slice(b, ...); b = give();` (dangling, dump-confirmed). Contrast: plain alias `b = a` correctly promotes to `std::string b = a;`.
- **Fix direction**: Route StrView/BytesView-typed inits through the same ViewVarInfo machinery: extend view_family_for_type (or the _infer_new_local_view_type entry) to cover view-typed inits, register the receiver of view-returning method calls as source_storage (requires receiver-borrow metadata on the stubs, e.g. return_borrows_from=[-1] for strip/removeprefix/etc.), and treat a view-returning call on an RVALUE receiver as initialized_from_owned (promote to owned). Sibling constructs not checked here: async-coroutine locals, with-statement targets, walrus bindings -- gap-sweep should verify they route through the same _infer_new_local_type path.

#### G6. is_dangling_return misses view-returning method calls: `return a.strip()` as StrView returns a view of a dead local; generator `yield make().strip()` returns view of temp

- **Location**: tpyc/sema/compatibility.py:2197-2221, lib/tpy/tpy/_core/_types.py:1675-1685,1780-1785
- **Severity / category**: critical / ub -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: The dangling-return check is asymmetric across sibling expressions: `def f() -> StrView: a = make(); return a[2:20]` is correctly rejected ('Cannot return StrView referencing a local or temporary'), because the TpySubscript branch (line 2192) recurses into the container. But `return a.strip()` and `return make().strip()` pass: the TpyMethodCall branch only flags methods returning OWNED str/String (line 2199) or Own[T]; strip/lstrip/rstrip/removeprefix/removesuffix stubs carry no return_borrows_from metadata, so the receiver-borrow loop (2213-2220) is skipped and the branch returns False at 2221. The emitted C++ returns a string_view into a local destroyed at return. The same metadata gap reaches the yield boundary: `def gen() -> Iterator[StrView]: yield make().strip()` emits `__next__()` returning `std::expected<std::string_view, StopIteration>` whose value views a temporary destroyed inside __next__ before the caller reads it.
- **Evidence**: Repro: `def g() -> StrView: a = make(); return a.strip()` emits `std::string_view g() { std::string a = make(); return ::tpy::str_strip(a); }` (no diagnostic). `def h() -> StrView: return make().strip()` emits `return ::tpy::str_strip(make());`. Generator: `def gen() -> Iterator[StrView]: yield make().strip()` emits `case S_INITIAL: { __state = S_RESUME_0; return ::tpy::str_strip(make()); }` -- the temp std::string from make() dies inside __next__. Meanwhile the slice form `return a[2:20]` errors correctly: 'Cannot return StrView referencing a local or temporary; use str or String to return an owned copy'.
- **Fix direction**: Stamp receiver-borrow facts (return_borrows_from = [-1]) on every view-returning str/bytes stub (strip family, removeprefix/removesuffix, BytesView variants, exceptions' __str__ -> StrView) so the existing TpyMethodCall return_borrows_from loop fires; audit the yield path uses the same check. Sibling to trace in gap-sweep: async `return`/`await` results of view type, and exceptions' `__str__(self) -> StrView` escaping the except block.

#### G7. Walrus bound in one f-string part / call arg, read in a later part: unsequenced C++ args read an uninitialized variable (observed garbage output)

- **Location**: tpyc/codegen_cpp/expressions.py:5965 (_gen_fstring emits parts as std::format args), tpyc/codegen_cpp/expressions.py:5967-6033 (_gen_named_expr hoists `int32_t y;` and emits the binding as an inline `(y = ...)` subexpression), tpyc/codegen_cpp/expressions.py:2933 (_gen_call joins gen_args with no sequencing)
- **Severity / category**: high / ub -- reproduced: yes -- found by `gap-bigfiles-second-pass`
- **Problem**: Python guarantees left-to-right evaluation of f-string parts and call arguments, so `f"{(y := x + 1)} and {y * 2}"` is well-defined (prints '6 and 12'). TPy lowers the walrus to a pre-declared local plus an inline C++ assignment expression, and places all f-string parts (or call args) as sibling arguments of one std::format/function call. C++ function arguments are indeterminately sequenced; GCC evaluates them right-to-left, so `y * 2` reads the never-assigned `y` -- an uninitialized read (UB), silently producing garbage. Same pattern for plain calls: `f(y := 7, y + 1)` emits `f((y = 7), (add_check(y, 1)))` (verified in dump). Sema marks `y` definitely-assigned for the later part because analysis is left-to-right, so no diagnostic fires. Distinct from the round-1 chained-comparison walrus finding (that was short-circuit skipping the assignment); this is unsequenced sibling-argument evaluation.
- **Evidence**: Repro /tmp/agents/x2/c16_walrus_fstring.py: `s = f"{(y := x + 1)} and {y * 2}"`. Generated: `int32_t y; std::string s = std::format("{} and {}", (y = (::tpy::add_check<int32_t>(x, 1))), (::tpy::mul_check<int32_t>(y, 2)));`. Built and ran: output `6 and 65534` (CPython: `6 and 12`). Plain-call variant /tmp/agents/x2/c30_walrus_call_args.py emits `f((y = 7), (::tpy::add_check<int32_t>(y, 1)))` -- same unsequenced shape.
- **Fix direction**: When a walrus appears in any but the last sibling subexpression of a call/f-string (or conservatively: always at multi-arg call sites), hoist the assignment through the existing pending-temps statement mechanism (ctx.temps) so it is sequenced before the call, and emit the bare name in the arg slot. Note the interaction with the round-1 'while-condition temps flushed once before the loop' bug: hoisted walrus statements in loop conditions must be re-emitted per iteration. Sibling positions to check in the gap sweep: method-call args, constructor args, subscript index lists, print() args.

#### G8. Two match statements in the same block scope redeclare __match_subject -> C++ build failure

- **Location**: tpyc/codegen_cpp/match.py:171, tpyc/codegen_cpp/match.py:297 (unscoped, un-numbered `__match_subject` binding emit)
- **Severity / category**: high / rejects-valid -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: Every match statement emits `auto& __match_subject = ...;` at the current indent with a fixed name and no enclosing braces. Two sequential matches in one function body (or any same-scope pair) produce 'conflicting declaration auto& __match_subject' from g++ -- a raw C++ error with no TPy diagnostic. End labels and default labels ARE counter-numbered (ctx.match_counter), only the subject binding is not. Nested matches survive only because case-arm braces create a new scope. This rejects utterly mainstream code (any function that matches twice).
- **Evidence**: Repro: two `match c:` statements over an enum (or any subject) in main(). Build output: `error: conflicting declaration 'auto& __match_subject' ... note: previous declaration as 'std::variant<Cat, Dog>& __match_subject'`. Reproduced with both enum and union subjects.
- **Fix direction**: Number the binding (`__match_subject_{ctx.match_counter}`) like the end labels, or wrap each non-resumable match in its own `{ }` block. Resumable (generator/async) match arms reference __match_subject across decomposed states -- verify the numbered name is threaded there too.
- **Verifier adjustment**: Bug is real and reproduced: two sequential enum matches in main() generate two same-scope `auto& __match_subject = c;` declarations; g++ fails with "error: conflicting declaration 'auto& __match_subject' ... note: previous declaration as 'tpyapp::...::Color& __match_subject'" and no TPy diagnostic. Locations verified (match.py:171 and :297 emit the fixed-name binding unscoped; end labels use ctx.match_counter, the subject binding does not). Severity adjusted down from the finder's framing tow...

#### G10. Nested def codegen/sema inherit the enclosing try/finally context: outer finally body inlined into the lambda; goto out of lambda to outer except label

- **Location**: tpyc/codegen_cpp/statements.py:3281-3347 (_gen_nested_def, no finally_stack/try_except_label reset), tpyc/sema/statements.py:2380 (_analyze_nested_def, try_except_error_type not cleared)
- **Severity / category**: high / miscompile -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: Two manifestations of the same context leak across the nested-def boundary. (1) Silent miscompile: a `return` inside a nested def defined within an enclosing try/finally walks the OUTER function's finally_stack, so the outer finally body is duplicated INTO the lambda before its return and executes on every call of the nested function. (2) Invalid C++: an @error_return call inside a nested def defined within an enclosing return-tier try is accepted by sema (ctx.try_except_error_type leaks into the nested body analysis, so the must-handle check passes) and codegen emits `goto __except_N;` from inside the lambda to a label in the enclosing function -- g++ rejects (and semantically the error could not unwind through a normal return type anyway).
- **Evidence**: Repro 1: def main(): try: def g() -> int: return 5; print(g()); print(g()) finally: print("done"). Generated lambda: `auto g = []() -> BigInt { std::cout << "done" << "\n"; return 5; };`. Observed run output: done/5/done/5/done (CPython: 5/5/done). Repro 2: nested def calling @error_return find(False) under enclosing `try ... except NotFound`: generated `auto g = []() -> BigInt { return ({ auto __er_2 = find(false); if (!__er_2.has_value()) goto __except_1; ... }); };` with __except_1 outside the lambda -- invalid C++, no TPy diagnostic.
- **Fix direction**: In _gen_nested_def, snapshot/clear and restore the per-function emission context (finally_stack, try_except_label, try_except_err_opt, in_except_tier, loop_else_labels) around the lambda body, exactly as the function-entry path initializes it. In sema _analyze_nested_def, clear try_except_error_type/in_except_tier for the nested body so unhandled @error_return calls inside nested defs are rejected with the normal must-handle diagnostic. Gap-sweep note: check the same leak for lambdas (TpyLambda) and comprehension bodies, and whether loop_else_labels leaks let a `break` in a nested def goto an outer else label.

#### G11. Return-tier try/except containing a suspension (yield/await) compiles but panics at runtime instead of dispatching to the handler

- **Location**: tpyc/codegen_cpp/resumable_cfg.py:1259-1268 (_build_try, `tier=stmt.tier or "throw"` -- tier carried but never honored), tpyc/codegen_cpp/gen_async.py:2747-2821 (_emit_try_region_catches lowers ALL handlers as C++ catch, sets in_except_tier="throw")
- **Severity / category**: high / crash -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: In a resumable frame (generator with multiple yields, or async def), a return-tier try/except (handler for a ReturnException, body calling an @error_return function) is accepted by sema, but the resumable lowering treats the handlers as throw-tier C++ catch clauses. The @error_return call site inside the resumed case loses the goto-except context (ctx.try_except_label is not active in the resumable path), so codegen falls back to the panic-unwrap shape `if (!tmp.has_value()) ::tpy::tpy_panic("unhandled error return")`. The emitted `catch (const NF&)` clauses can never fire because return-tier errors are never thrown. Net effect: valid, sema-accepted code panics at runtime on the error path. Both the generator and async siblings reproduce.
- **Evidence**: Repro (generator): try: yield 1; x = get(False); yield x except NF: yield 99 -- where get is @error_return(NF). Generated case S_RESUME_0: `auto __try_tmp_1 = get(false); if (!__try_tmp_1.has_value()) ::tpy::tpy_panic("unhandled error return"); ... } catch (const NF&) { ... yield 99 path ... }`. Observed run: `TurboPython panic: unhandled error return` (CPython: 1, 99). Async repro (await asyncio.sleep(0) then get(False) in try/except NF): same panic.
- **Fix direction**: Either teach the resumable lowering to honor TryRegion.tier == "return" (thread a per-region except target so @error_return call sites emit the error-dispatch branch into the handler BB instead of panic), or have sema reject return-tier try blocks that contain a suspension with a clean "not yet supported" diagnostic until the lowering exists. The for-loop StopIteration consumption path is separate and works; only explicit return-tier try/except is affected.

#### G12. Walrus in ternary branch marked definitely-assigned: uninitialized read (UB) where CPython raises NameError

- **Location**: tpyc/sema/expressions.py:2514-2541
- **Severity / category**: high / ub -- reproduced: yes -- found by `gap-siblings`
- **Problem**: Sibling of the round-1 'walrus in chained comparison' finding, on the ternary axis. _analyze_if_expr saves/restores only narrowed_types around branch analysis -- the comment at 2524-2525 says 'ternary doesn't create vars, so we only need to save/restore narrowing', which is false with walrus. A `(t := v)` in the then-branch marks t definitely-assigned unconditionally, so a later read of t passes sema even on the else path. The and/or path (expressions.py:777-795) explicitly rolls back walrus assignments; the ternary join does not.
- **Evidence**: Repro /tmp/agents/sib/t1_walrus_ternary.py: `y = (t := 5) if c else 0; print(t)` with main(False). Zero diagnostics. Generated C++: `int32_t t; int32_t y = ((c) ? ((t = 5)) : (0)); std::cout << t << "\n";` -- when c is false, t is read uninitialized (UB for the int32_t; for non-trivial types worse). CPython: NameError. Code: _analyze_if_expr (2514) does `saved_narrowed = dict(self.ctx.func.narrowed_types)` and restores only that; definitely_assigned (InitTracker) is never saved/rolled back across the two branch analyses.
- **Fix direction**: Save/rollback the InitTracker assignment state around each branch and merge as conditional (assigned only if assigned in BOTH branches), exactly like the && / || rollback at 777-795. Sibling not checked: walrus in a guarded match arm was verified OK (rejected correctly), walrus in comprehension conditions untested.

#### G14. Owned temporary appended into an explicit view container (list[StrView]) is accepted silently -> stored dangling view

- **Location**: tpyc/sema/calls.py (no check at container-insert coercion), repro-driven
- **Severity / category**: high / ub -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: xs: list[StrView] = []; xs.append(make()) compiles with no diagnostic: the owned std::string temporary returned by make() is implicitly converted to std::string_view and pushed into vector<string_view>; the temporary dies at the end of the statement, so xs[0] is permanently dangling. The pinned-view warning system only covers name-alias bindings (`v: StrView = name`); container inserts (and dict[StrView,...] keys, set[StrView] elements, StrView field assignment from rvalues) have no rvalue-into-view-storage check. This is a user-opt-in type (StrView), but rejecting an RVALUE source is cheap and unambiguous -- a temporary can never outlive the container.
- **Evidence**: Repro: `xs: list[StrView] = []` then `xs.append(make())` emits `std::vector<std::string_view> xs = ...; xs.push_back(make());` -- make() returns std::string by value; the span/view dangles immediately. print(xs[0]) reads freed memory. No error or warning emitted.
- **Fix direction**: At coercion into view-typed storage (container element, dict key, field), reject rvalue owned-string/bytes sources outright (mirror the existing 'Cannot return StrView referencing a local or temporary' wording). Gap-sweep siblings: Span[T] element containers, dict[StrView, V] keys, set[StrView], BytesView containers.

#### G15. Generic function called with a borrow-form union/Optional arg: template instantiated with storage form, arg passed unconverted -> ill-formed C++, no TPy diagnostic

- **Location**: tpyc/sema/calls.py:4713-4793 (_analyze_generic_function_call infers T as the declared storage-form type and records no coercion), tpyc/codegen_cpp/expressions.py:2798-2806 (resolved_ptype = substitute_type_params; the _gen_optional_ptr_arg/_gen_union_arg cascade converts toward borrow-form params, never toward the val_or_ref_t storage ABI), tpyc/codegen_cpp/expressions.py:2925-2933 (explicit template args via type_to_cpp_stored)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-bigfiles-second-pass`
- **Problem**: Generic TPy functions are emitted as `val_or_ref_t<T> f(param_val_or_ref_t<T> v)` and call sites pass explicit storage-form template args (type_to_cpp_stored). For a plain record arg, borrow form and storage form coincide (T&), so this works. But a union-typed param (`u: A | B`, borrow form std::variant<A*,B*>) or pointer-repr Optional local (`ob: Box | None`, borrow form Box*) is passed RAW into a parameter of type `std::variant<A,B>&` / `std::optional<Box>&` -- no to_value_variant/ptr_to_optional conversion is emitted, and the result is bound back into a borrow-form local unconverted. Sema accepts silently (arg type equals substituted param type at the TpyType level, so no coercion is recorded); the failure surfaces as a g++ template error wall. This is the borrow/storage duality leaking through the generic-call ABI: even with conversion, a converted temp could not bind to the non-const `T&` param, so the call should likely be rejected or the generic ABI extended with a borrow-form instantiation.
- **Evidence**: Repro /tmp/agents/x2/c4_generic_method_union.py: `def ident[T](v: T) -> T: return v` called as `w = ident(u)` with `u: A | B`. Generated: `std::variant<A*, B*> w = ident<std::variant<A, B>>(u);`. Built: g++ `error: invalid initialization of reference of type 'std::variant<A,B>&' from expression of type 'std::variant<A*, B*>'`. Optional sibling /tmp/agents/x2/c35_generic_optional_arg.py generates `Box* w = ident<std::optional<Box>>(ob);` -- same shape, same failure mode.
- **Fix direction**: Decide the contract in sema: either reject borrow-form union/pointer-repr-Optional args to type-param slots with a real diagnostic, or define the generic ABI for them (e.g. instantiate with the borrow form `std::variant<A*,B*>` / `T*` and substitute the return accordingly, mirroring force_pointer_repr -- note round 1 found make_union already drops that flag). Untested siblings for the gap sweep: generic METHODS with union/Optional args, generic constructors (Box(union_local)), and Own[T] params where T infers to a union.
- **Verifier adjustment**: Facts fully confirmed by independent repro /tmp/agents/verify/f2_generic_union.py: `def ident[T](v: T) -> T` called with `u: A | B` generates `std::variant<A*, B*> w = ident<std::variant<A, B>>(u);` and g++ fails with `invalid initialization of reference of type 'std::variant<A,B>&' from expression of type 'std::variant<A*,B*>'` (param_val_or_ref_t<std::variant<A,B>> = std::variant<A,B>&). No TPy diagnostic; sema accepts (TpyType-level types match, no coercion recorded; _analyze_generic_funct...

#### G16. @error_return call inside a generator expression: error-unwrap `goto` targets a label outside the enclosing lambda -> C++ build failure, no TPy diagnostic

- **Location**: tpyc/codegen_cpp/expressions.py:2536-2547 (_maybe_error_return_unwrap emits `goto {ctx.try_except_label}` with no lambda-boundary awareness), tpyc/codegen_cpp/expressions.py:4714-4880 (_gen_generator_expression emits the element expression inside the make_generator lambda body), tpyc/sema/calls.py:4685-4712 (_check_error_return_handled accepts the lexical try/except as 'handled')
- **Severity / category**: medium / crash -- reproduced: yes -- found by `gap-bigfiles-second-pass`
- **Problem**: The error-return unwrap machinery has three context modes (goto enclosing try label, propagate via return make_unexpected, panic). The goto and propagate modes both assume the call expression is emitted in the same C++ function as the handler/return slot. A generator expression compiles its element into a nested lambda passed to ::tpy::make_generator; an @error_return call there under an enclosing try/except emits `goto __except_1;` inside the lambda while the label lives in the outer function -- ill-formed C++ ('label used but not defined'), with no TPy diagnostic. The propagate mode (@error_return caller containing the genexpr) would be equally ill-formed (return type mismatch inside the lambda). List/dict/set comprehensions are unaffected (verified: they lower to a statement-expression in the same function, /tmp/agents/x2/c38_er_listcomp.py builds the same goto legally). This is a direct sema/codegen mirroring drift: sema's notion of 'handled' is lexical (try/except in scope), codegen's mechanism is function-local (labels/returns).
- **Evidence**: Repro /tmp/agents/x2/c11_er_genexpr.py: `total = sum(double(x) for x in xs)` under try/except where double is @error_return. Generated (inside the make_generator lambda): `return std::optional<int32_t>(({ auto __er_2 = double_(x); if (!__er_2.has_value()) goto __except_1; ... }));`. Built: g++ `error: label '__except_1' used but not defined`.
- **Fix direction**: Either have sema reject @error_return calls in genexpr position with a clear diagnostic (matching the existing await-in-comprehension rejection), or make the unwrap lambda-aware: inside a genexpr lambda, convert the error into the exception-throw tier (or thread an out-param error slot back to the enclosing frame). Check the same hazard for the simple-generator lambda peephole and for nested defs emitted as lambdas (untested here -- flag for gap sweep).

#### G17. Out-of-order kwargs reordered into positional order: argument side effects run in declaration order (and C++-unspecified order generally), diverging from Python's written-order guarantee

- **Location**: tpyc/sema/calls.py:215-231 (resolve_kwargs builds the result list in param order), tpyc/sema/calls.py:599-622 (_resolve_call_kwargs/_resolve_call_kwargs_init rewrite expr.args and clear expr.kwargs), tpyc/sema/methods.py:385, tpyc/sema/methods.py:452 (methods share the same helper)
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `gap-bigfiles-second-pass`
- **Problem**: CPython evaluates call arguments left-to-right as written, including keyword arguments. resolve_kwargs physically reorders kwarg expressions into positional-parameter order before codegen, so `f(b=log.tick(2), a=log.tick(1))` is emitted as `f(log.tick(1), log.tick(2))` -- the side effects of the b-arg and a-arg expressions are swapped relative to source order (value-to-parameter mapping is correct; only evaluation ORDER diverges). Compounding it, C++ sibling-argument evaluation order is unspecified (GCC: right-to-left), so even plain positional `f(g1(), g2())` can run g2 before g1 -- a silent CPython divergence for any multi-arg call with side-effecting args. Neither divergence is documented (grep of docs/ for evaluation-order caveats only finds the multi-target-assign note at LANGUAGE_FEATURES.md:3353). Affects free functions, methods, and constructors alike (shared helper).
- **Evidence**: Repro /tmp/agents/x2/c20_kwarg_order.py: `r = f(b=log.tick(2), a=log.tick(1))` where tick appends a digit to log.n. Generated: `int32_t r = f(log.tick(1), log.tick(2));`. CPython evaluates tick(2) then tick(1) (log.n == 21); the emitted code evaluates in C++-unspecified order over the REORDERED list (left-to-right would give 12).
- **Fix direction**: For calls where more than one argument expression is potentially side-effecting (non-literal, non-name), hoist arguments into sequenced temps in written order before the call (the temps mechanism exists), then pass the temps positionally. At minimum document the divergence in LANGUAGE_FEATURES.md. Note this is also the substrate that makes the walrus-in-args finding UB rather than merely reordered.

#### G18. Nested ternary joining T and Optional[T] rejected ('Incompatible types in ternary expression: Box and Box | None'), even under a declared Optional return type

- **Location**: tpyc/sema/expressions.py:2641 (raise site in _ternary_common_type; sibling file flagged for gap sweep -- found by tracing the codegen ternary path from expressions.py:6102)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-bigfiles-second-pass`
- **Problem**: The idiomatic two-level conditional `b if f == 0 else (ob if f == 1 else None)` fails to type: the inner ternary produces Box | None via the T+None special case, but the outer join has no T + Optional[T] -> Optional[T] rule, and the declared return type `-> Box | None` is not consulted at the join. This is the same root family as round 1's 'ternary under a declared union annotation is rejected' (join ignores the hint, only union-producing rule is T+None), but this manifestation needs no annotation at all and arises from plain nesting -- any chained conditional that ends in None hits it. CPython accepts trivially.
- **Evidence**: Repro /tmp/agents/x2/c8_nested_ternary.py: `def pick(flag: Int32, b: Box, ob: Box | None) -> Box | None: return b if flag == 0 else (ob if flag == 1 else None)` -> `c8_nested_ternary.py:10: error: Incompatible types in ternary expression: 'Box' and 'Box | None'`.
- **Fix direction**: Extend _ternary_common_type with T + Optional[T] -> Optional[T] (and the symmetric case), and/or consult the expression type hint at the join as round 1 already proposed. Any fix must also pick a coherent borrow/storage form for the joined Optional (see round 1's mixed-form ternary finding) -- fixing sema alone will expose the codegen form-mixing bug.

#### G19. f-string rejects Optional/union-with-None parts that print() handles -- sibling inconsistency, CPython prints 'None'

- **Location**: tpyc/sema/expressions.py:3601 (rejection raise), tpyc/codegen_cpp/expressions.py:5905-5942 (_gen_fstring part-type dispatch has a UnionType arm but the sema gate rejects None-bearing unions first); print sibling via ::tpy::print_optional (codegen builtins gen_print)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-bigfiles-second-pass`
- **Problem**: `print(ob)` where `ob: Box | None` compiles to `::tpy::print_optional(ob)` and prints 'None' or the object -- full CPython parity. The f-string sibling `f"box={ob}"` is rejected with 'Type Box | None cannot be used in f-string (no __str__ or __repr__ method)', even though the runtime helper that print uses would serve directly. Idiomatic logging code (`f"got {maybe_thing}"`) must be rewritten. Clean diagnostic + trivial workaround, hence low, but it is a sibling-construct gap of exactly the kind the focus asks to flag.
- **Evidence**: Repro /tmp/agents/x2/c45_fstring_optional.py: `print(f"box={ob} num={oi}")` -> `error: Type Box | None cannot be used in f-string (no __str__ or __repr__ method)`. Control /tmp/agents/x2/c46_print_optional.py: `print(ob)` generates `std::cout << ::tpy::print_optional(ob)` and compiles.
- **Fix direction**: In _gen_fstring, route OptionalType/None-bearing-union parts through the same print_optional/__str__ visitor print uses (mind the borrow vs storage source form -- round 1 found the Optional match/ternary paths assume pointer form from the type alone); lift the sema rejection for types print accepts.
- **Verifier adjustment**: Finding is real and independently reproduced; only the severity needs correction. Repro /tmp/agents/verify2/f1_fstring_opt.py (class Box with __str__, param ob: Box | None, f"box={ob} num={oi}") -> 'error: Type Box | None cannot be used in f-string (no __str__ or __repr__ method)' raised at tpyc/sema/expressions.py:3599-3604; _is_fstring_renderable (lines 3549-3550) recurses union members and the NoneType member fails all renderability checks. Control /tmp/agents/verify2/f1_print_opt.py: prin...

#### G20. Membership `in` with pointer-repr (narrowed Optional) reference-type LHS emits ill-formed C++

- **Location**: tpyc/codegen_cpp/expressions.py:1637, tpyc/codegen_cpp/expressions.py:1677, tpyc/codegen_cpp/expressions.py:1685
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-coverage`
- **Problem**: Round-1 codegen-expr flagged the `in` operator with an indirect reference-type LHS as suspicious but could not build a failing case. Reproduced: when the LHS of `x in container` is a borrow-form pointer local (a `C | None` param narrowed by `if c is not None:`), gen_expr at line 1637 emits the raw name without dereferencing. Only the RHS gets `is_indirect_name` handling (line 1651). The NativeIterable path then emits `std::ranges::contains(xs, c)` comparing `std::vector<C>` elements against a `const C*` (line 1677), which fails template constraint satisfaction; the universal `__iter__` fallback (line 1685, `unwrap_ref(*__r) == {left}`) has the identical missing deref. TPy's frontend accepts the program with no diagnostic; the user gets a multi-page g++ concepts error. The tuple-needle special case at 1644-1648 lifts pointer-repr tuple elements via tuple_to_storage but the plain pointer-repr scalar LHS has no analogous handling -- a textbook sibling-construct gap. Unverified siblings: pointer-variant union LHS (`(A|B) in list[A|B]`) and the resolved_contains path at 1655 likely share the missing deref.
- **Evidence**: Repro (/tmp/agents/sweep/in_ptr_lhs.py):
  def f(c: C | None, xs: list[C]) -> bool:
      if c is not None:
          return c in xs
      return False
Generated C++:
  bool f(const C* c, const std::vector<C>& xs) {
      if ((c != nullptr)) {
          return std::ranges::contains(xs, c);
      }
  ...
Observed build failure (uv run tpy):
  /usr/include/c++/14/concepts:360:25: note: the expression 'is_invocable_v<_Fn, _Args ...> [with _Fn = std::ranges::equal_to&; _Args = {C&, const C*&}]' evaluated to 'false'
- **Fix direction**: Before emitting the comparison, check the LHS expression's repr (narrowed pointer-repr Optional / indirect local) and emit `(*c)` -- the same treatment line 1651 gives the RHS, or reuse the storage-form lifting strategy the tuple-needle branch at 1644 already applies. Audit the resolved_contains (1655) and string `.find` (1660) branches for the same gap.
- **Verifier adjustment**: Reproduced end-to-end. Repro (/tmp/agents/verify/f1_in_ptr_lhs.py, note class C must define __eq__ or sema rejects with an Equatable diagnostic first): `def f(c: C | None, xs: list[C]) -> bool: if c is not None: return c in xs`. Generated C++: `bool f(const C* c, const std::vector<C>& xs) { if ((c != nullptr)) { return std::ranges::contains(xs, c); } ... }`. Full build fails: "error: no match for call to (const std::ranges::__contains_fn) (const std::vector<...C>&, const ...C*&)" with the inv...

#### G21. Generator for-loop tuple-unpack over dict.items() emits `&(*it++)` on a prvalue iterator -- ill-formed C++

- **Location**: tpyc/codegen_cpp/gen_generators.py:699-722, runtime/cpp/include/tpy/ordered_map.hpp:139-142
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-coverage`
- **Problem**: Round-1 declared resumable-frame variants of container boundaries unprobed. Reproduced: `for k, c in d.items(): ... yield k` inside a generator, where the dict value type is a reference type. The pointer-form aliasing path in gen_generators.py (lines 699-722) decides that non-value tuple members alias the container element via `__for_tup_0 = &(*((*__for_it_0))++)`. This assumes the iterator's `operator*` returns a reference into stable storage. `ordered_map::tuple_items_iterator_impl::operator*` (ordered_map.hpp:142) returns `value_type` BY VALUE (`return {node_->key, node_->value};` -- it also copies K and V out of the node). Taking the address of that prvalue is ill-formed; g++ rejects with 'taking address of rvalue'. TPy's frontend accepts with no diagnostic. This is the frame sibling of round-1's sync items() silent-copy finding: same prvalue-iterator root, but in generators it surfaces as a hard C++ failure instead of lost mutations. Any other iterator with a prvalue `operator*` routed through this pointer-form path (e.g. zip-style adapters) would hit the same wall; async `for ... in d.items()` with an await in the body shares the resumable-frame emission and is presumed equally affected (not separately built).
- **Evidence**: Repro (/tmp/agents/sweep/items_in_frame.py):
  def gen(d: dict[int, C]) -> Iterator[int]:
      for k, c in d.items():
          c.v = c.v + 1
          yield k
Generated frame body:
  __for_tup_0 = &(*((*__for_it_0))++);
  ...
  c = &(std::get<1>(__tup_1));
Observed build failure:
  items_in_frame.cpp:25:25: error: taking address of rvalue [-fpermissive]
     25 |         __for_tup_0 = &(*((*__for_it_0))++);
Runtime iterator (ordered_map.hpp:142):
  value_type operator*() const { return {node_->key, node_->value}; }
- **Fix direction**: The pointer-form decision must be conditioned on the source iterator yielding lvalue references (a TypeDef-level 'elements are stable lvalues' fact, per the CLAUDE.md push toward library-declared metadata), falling back to value storage otherwise. The real semantic fix for items() is a proxy-reference items iterator (pair of refs into the node) so both the sync silent-copy and this frame variant get CPython aliasing; note even with -fpermissive the current code would mutate a dead temporary (UB), so the value-storage fallback alone silently diverges from CPython.
- **Verifier adjustment**: Reproduced end-to-end. Repro (/tmp/agents/verify/f2_items_in_frame.py): generator `for k, c in d.items(): c.v = c.v + 1; yield k` with d: dict[int, C], C a reference type. Generated frame body contains `__for_tup_0 = &(*((*__for_it_0))++);` and `c = &(std::get<1>(__tup_1));`; full build fails with "error: taking address of rvalue [-fpermissive]" at that line, no TPy diagnostic. Root cause verified: gen_generators.py:699-722 selects pointer-form aliasing for non-value tuple-unpack members assu...

#### G22. Calling a generic function's `T | None` param with None or a value-type arg emits ill-formed C++ (pointer-repr dropped at call boundary)

- **Location**: tpyc/codegen_cpp/expressions.py:1138-1153
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-coverage`
- **Problem**: This validates round-1 typesys' low-confidence suspicion about force_pointer_repr drop sites, at the call boundary rather than in substitute_type_params (which I audited and found correct -- type_ops.py:669-673 preserves pointer repr). A generic `def f[T](x: T | None)` renders its param as `const T*` (TypeParamRef inner is non-value, so OptionalType.uses_pointer_repr() is true). But at the call site: (a) a literal `None` arg hits the TpyNoneLiteral branch at expressions.py:1144-1145, which returns `std::nullopt` for ANY OptionalType target without consulting uses_pointer_repr(), producing `f<std::monostate>(std::nullopt)` against a `const std::monostate*` param; (b) a value-type arg `f(1)` is passed raw (`f<int32_t>(1)`) with no temporary materialization or address-of, producing int-to-pointer. Both fail at the C++ stage with no TPy diagnostic. A reference-type arg works (`f<C>(&(c))` is emitted), confirming the coercion exists but skips value-type instantiations and None literals -- exactly the borrow/storage-form boundary bug class. So the entire feature 'generic function with optional parameter' is unusable with None or primitives.
- **Evidence**: Repro (/tmp/agents/sweep/optional_generic_none.py):
  def f[T](x: T | None) -> bool:
      return x is None
  f(None); f(1)
Generated:
  template<typename T> bool f(const T* x);
  f<std::monostate>(std::nullopt)   // None arg
  f<int32_t>(1)                     // value arg
Observed build failures:
  error: cannot convert 'const std::nullopt_t' to 'const std::monostate*'
  error: invalid conversion from 'int' to 'const int*'
Control: f(c) with class C emits f<C>(&(c)) and is fine.
- **Fix direction**: In the TpyNoneLiteral branch, return "nullptr" when unwrapped.uses_pointer_repr() (line 1144). For value-type args against a pointer-repr Optional param, materialize the argument into a temporary and pass its address (the statement-expression hoisting used elsewhere for call args), or instantiate the callee with a value-form optional. Sibling to audit: pointer-variant generic unions (`x: T | U` instantiated at value types) likely share both halves.
- **Verifier adjustment**: Reproduced end-to-end. Repro (/tmp/agents/verify/f3_optional_generic.py): `def f[T](x: T | None) -> bool: return x is None` called as f(None) and f(1). Generated: `bool f(const T* x)` (pointer-repr param) but call sites emit `f<std::monostate>(std::nullopt)` and `f<int32_t>(1)`. Full build fails with exactly the claimed errors: "cannot convert 'const std::nullopt_t' to 'const std::monostate*'" and "invalid conversion from 'int' to 'const int*'". Code matches: expressions.py:1144-1145 TpyNoneL...

#### G23. Await lifter evaluates assignment-target awaits before value awaits, inverting Python evaluation order

- **Location**: tpyc/codegen_cpp/gen_async.py:828-848, tpyc/parse/nodes.py:756-757
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `gap-coverage`
- **Problem**: Round-1 parser flagged (unverified) that the await desugar iterates the assign target before the value. Reproduced: for `d[await key()] = await val()`, CPython evaluates the RHS first ('val evaluated' then 'key evaluated'); TPy's _collect_nested_awaits walks `stmt.exprs()` in field order, and TpyAssign.exprs() returns [target, value], so the lifted awaits run target-first. The generated state machine polls __coro_key in S_RESUME_0 and __coro_val in S_RESUME_1 -- observable side effects in the wrong order, silently. Note the plain sync case `d[key()] = val()` coincidentally matches Python because C++17 sequences the RHS of `=` before the LHS, so ONLY the async-lifted form diverges -- a nasty sync/async behavioral split. TpyAugAssign's [target, value] order is correct for Python aug-assign semantics; only plain Assign is wrong.
- **Evidence**: Repro (/tmp/agents/sweep/await_order.py):
  d[await key()] = await val()
Generated poll order:
  case S_RESUME_0: poll __sub_0 (=__coro_key, prints "key evaluated") ...
  case S_RESUME_1: poll __sub_1 (=__coro_val, prints "val evaluated")
CPython output (observed):
  val evaluated
  key evaluated
Root (parse/nodes.py:756): def exprs(self): return [self.target, self.value]
Collector (gen_async.py:846-848): for e in stmt.exprs(): walk_expr(e)  # field order
- **Fix direction**: In _collect_nested_awaits (or via a per-statement-kind expr ordering), visit TpyAssign.value before TpyAssign.target. Audit other stmt kinds whose exprs() order differs from Python evaluation order (e.g. chained/multiple-target assigns, annotated assigns); keep TpyAugAssign target-first.
- **Verifier adjustment**: Bug is real but the finding's exact repro does NOT compile: `d[await key()] = await val()` is rejected by sema with "await result can only be bound to a simple name in v1; field/index targets are not yet supported" (the top-level value await with an index target). The divergence manifests when the value-side await is NESTED in a larger expression: `d[await key()] = (await val()) + 0` compiles, and the generated state machine (verified in dump) declares `std::optional<__coro_key> __sub_0; std:...

#### G24. except-as binds the exception as `const&`; mutating the caught exception emits ill-formed C++

- **Location**: tpyc/codegen_cpp/statements.py:3436-3453
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-coverage`
- **Problem**: Round-1 hunt-silent-copy skipped exception binding entirely. _emit_except_handler_header always emits `catch (const {cpp_type}& {binding})`. Python allows mutating a caught exception (`except E as e: e.context = ...` is a common enrichment idiom); in TPy the handler body's field assignment goes through the const reference and g++ rejects it ('passing const tpy::BigInt as this argument discards qualifiers'). Sema neither rejects the mutation with a TPy diagnostic nor binds non-const, so the user gets a raw C++ error. The docstring notes this helper is shared by the async-await try/except path in gen_async.py, so the async sibling is equally affected.
- **Evidence**: Repro (/tmp/agents/sweep/except_alias.py):
  try:
      raise MyError()
  except MyError as e:
      e.n = 7
Generated:
  } catch (const MyError& e) {
      e.n = ::tpy::BigInt(7);
Observed build failure:
  except_alias.cpp:12:34: error: passing 'const tpy::BigInt' as 'this' argument discards qualifiers [-fpermissive]
Emitter (statements.py:3451):
  out.write(f" catch (const {cpp_type}& {binding}) {{\n")
- **Fix direction**: Either catch by non-const reference when sema records the handler body as mutating the binding (mutation facts already exist for params), or always catch by non-const `E&` (legal C++, no copy). If mutation of caught exceptions is meant to be unsupported, sema must reject it with a proper diagnostic instead of leaking a g++ error. Apply to both the sync emitter and the gen_async.py sharer.

#### G25. Callable objects (__call__) cannot be invoked through attribute access, and unannotated rebinding loses callability

- **Location**: tpyc/sema/methods.py:912, tpyc/sema/calls.py:910-930
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-coverage`
- **Problem**: Round-1 sema-calls flagged __call__-dunder routing for the method-call path as unverified. Two reproduced rejections of valid Python that docs/LANGUAGE_FEATURES.md:6110 claims works ('call results of arbitrary expressions ... any expression evaluating to ... a type with __call__'): (1) `h.a(5)` where field `a` holds a callable object errors 'Cannot call method a on type Holder' -- the method-call resolver (methods.py, error at 912) walks methods and __deref__ chains but never falls back to 'attribute is a field whose type defines __call__'. (2) `g = h.a; g(5)` errors "'g' is not callable" -- the variable-callee path at calls.py:910-930 strips Own (913) and Send/Sync markers (916) from var_type but not Ref/readonly wrappers, so the borrow-form local inferred from a field access (Ref[Adder]) fails the isinstance(var_type, NominalType) check at 922 and never reaches the __call__ lookup. Control: `g: Adder = h.a; g(5)` works and correctly emits an aliasing `Adder& g = h.a;`. Direct local (`f = Adder(1); f(2)`) and temporary (`Adder(3)(4)`) forms work.
- **Evidence**: Repro (/tmp/agents/sweep/call_dunder.py):
  h = Holder(); print(h.a(5))     -> error: Cannot call method 'a' on type Holder
  g = h.a; print(g(5))            -> error: 'g' is not callable
  g: Adder = h.a; print(g(5))     -> compiles: Adder& g = h.a; g.__call__(5)
calls.py:913-922 strips OwnType and unwrap_send_sync but no unwrap_ref_type/unwrap_readonly before:
  if isinstance(var_type, NominalType):
      record = ... get_method_overloads_with_parents(record, "__call__")
- **Fix direction**: (1) In the method-call resolver, before raising at methods.py:912, check whether `expr.method` resolves to a field of the receiver whose (unwrapped) type defines __call__, and route to the dunder-call analysis with the field access as callee. (2) At calls.py:911-916, unwrap Ref/readonly alongside Own and Send/Sync before the is_fn_type/CallableType/NominalType cascade. Sibling to check: subscript callees holding ref-wrapped callables (calls.py:928) and calls through self.field inside methods.

#### G26. Set-literal insert of a reference-type object silently copies with zero diagnostics (CPython aliases)

- **Location**: tpyc/codegen_cpp/expressions.py:4492-4520
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `gap-coverage`
- **Problem**: Round-1 hunt-silent-copy explicitly did not probe set-element aliasing of custom objects. Reproduced end-to-end: `s = {c}` copies the live object into the set's inline storage (`ordered_set<C>({c})` copy-constructs), then `c.v = 99` mutates only the original; iterating the set observes the stale copy. TPy prints 'in set: 0', CPython prints 'in set: 99' -- a parity-blind divergence with no warning, no move, and no consumed-var tracking on `c`. The list-literal sibling (`xs = [c]`) compiles to the same silent copy with the same absence of diagnostics (verified via tpyc, exit 0, no warnings) -- if round-1 filed the list case, this confirms set shares the root; the inline-element storage design makes the copy intentional, but the complete absence of a copy diagnostic at the container-literal boundary (while @nocopy types would hard-error) leaves the CPython-aliasing divergence invisible, exactly the failure mode CLAUDE.md's test-adequacy section warns about.
- **Evidence**: Repro (/tmp/agents/sweep/set_insert_alias.py):
  c = C(1)
  s = {c}
  c.v = 99
  for x in s: print("in set:", x.v)
Generated: ::tpy::ordered_set<C> s = ::tpy::ordered_set<C>({c});  // copy of c
Observed runtime (built binary): in set: 0
CPython: in set: 99
Diagnostics from `uv run tpyc`: none (exit=0).
- **Fix direction**: Emit a copy warning (or require explicit .clone()/move) when a still-live reference-type lvalue is inserted into a container literal -- mirroring whatever resolution is chosen for the list sibling so list/dict-value/set stay consistent. Last-use analysis could allow a silent move instead when the source is dead after the literal.

#### G27. Union-typed field or container element passed to a union parameter misses the storage->borrow (to_ptr_variant) conversion -> C++ build failure

- **Location**: tpyc/codegen_cpp/expressions.py:334-385 (_gen_union_arg: 'already_union' sources return None; the to_ptr_variant lift at :376 is gated to TpyName + needs_to_ptr_variant_lift)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: A non-value union value in a field or list element is storage form (std::variant<Cat, Dog>); a union parameter is borrow form (std::variant<Cat*, Dog*>). _gen_union_arg treats any arg whose sema type is already UnionType as 'already a union -- use default gen_call_arg', but only TpyName args with an Own[A|B] declared type get the ::tpy::to_ptr_variant lift. A field access (f(h.pet)) or subscript (f(xs[0])) source therefore passes the value variant straight to the pointer-variant param and g++ rejects it with a raw 'could not convert' error -- no TPy diagnostic. This is exactly the borrow/storage boundary the project calls its recurring bug class, missing at the call-argument boundary for non-name sources. Unions with a None member (variant<monostate, Cat*, Dog*>) fail identically.
- **Evidence**: Repro: class Holder: pet: Dog | Cat; def f(p: Dog | Cat) -> str: ...; f(h.pet). Generated call: `f(h.pet)` with param `const std::variant<Cat*, Dog*> p`. Build: `error: could not convert 'h....pet' from 'variant<Cat, Dog>' to 'variant<Cat*, Dog*>'`. Same failure for f(xs[0]) with xs: list[Dog | Cat], and for Dog | Cat | None fields.
- **Fix direction**: In _gen_union_arg, when the param is ptr-variant and the arg renders in storage form (field/subscript/deref of value variant -- decide by the arg's C++ shape, not by TpyName), emit ::tpy::to_ptr_variant(lvalue). Sibling boundaries to sweep: union args to METHODS and constructors, and union fields passed as readonly params (ptr_variant_to_const path has the same TpyName-shaped gating).
- **Verifier adjustment**: Bug is real and reproduced: `f(h.pet)` with field `pet: Dog | Cat` and param `p: Dog | Cat` generates a direct `f(h.pet)` call against `const std::variant<Cat*, Dog*> p`; build fails with "error: could not convert 'h....pet' from 'variant<Cat, Dog>' to 'variant<Cat*, Dog*>'", no TPy diagnostic. Code verified at expressions.py:334-385: `already_union` (line 353-357) is true for a storage-form field/subscript source, the to_ptr_variant lift at :376 is gated to `isinstance(arg, TpyName) and need...

#### G28. Or-pattern with as-binding (`case Dog() | Cat() as p:`): binding silently dropped in codegen and mistyped in sema

- **Location**: tpyc/sema/match.py:613-626 (TpyAsPattern falls to bindings[name] = subject_type for non-class inner), tpyc/codegen_cpp/match.py:682-720 (_gen_match_switch_union TpyOrPattern branch never emits the outer as_name)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: Valid Python: `case Dog() | Cat() as p:` binds p to the matched object. TPy sema types p as the FULL subject union (Bird | Cat | Dog) instead of the matched alternatives (Cat | Dog), and the union-switch codegen's or-pattern branch never declares p at all. If the body uses p, the generated C++ references an undeclared identifier (raw g++ error, no TPy diagnostic); if p is unused, it compiles silently with the binding dropped. The polymorphic-dispatch sibling documents 'or-pattern alternatives bind no variables', but this is the OUTER as-binding, which has one well-typed value (the union of alternatives) and CPython requires it.
- **Evidence**: Repro: match a: case Dog() | Cat() as p: return h(p). With h(p: Dog|Cat|Bird) sema accepts (p typed as full union -- confirmed via 'got Bird | Cat | Dog' in a mismatch message), and codegen emits `case 2: case 1: { return h(p); }` with no declaration of p anywhere. With h(p: Cat|Dog) sema rejects with 'Type mismatch ... expected Cat | Dog' even though p can only be Dog or Cat.
- **Fix direction**: Sema: as-over-or should bind the union of the alternatives' resolved types. Codegen: the or-branch needs the same per-alternative duplication used for field bindings (bind `p` from std::get<idx> per case label), or route to the guarded path. Check siblings: as-over-or on enum/primitive/Optional subjects, and `case Dog() | Cat() as p` under the guarded (goto) union path.
- **Verifier adjustment**: Bug is real and reproduced. With `case Dog() | Cat() as p: return h(p)` (h takes Dog|Cat|Bird), sema accepts (p typed as the FULL subject union) and codegen emits `case 2: case 1: { return h(p); break; }` -- `p` is never declared anywhere in the generated C++, so the build would fail with an undeclared-identifier g++ error; if p is unused the binding is silently dropped (semantically harmless at runtime since nothing reads it, but still a dropped binding). Code verified: sema/match.py:613-626...

#### G29. Bare capture `case x:` on a reference-type union subject silently copies the variant (CPython aliases; plain assignment and as-patterns alias)

- **Location**: tpyc/codegen_cpp/statements.py:4718 (_emit_branch_decls pre-declares storage-form variant), tpyc/codegen_cpp/match.py:996-1001 (_emit_binding 'x = __match_subject' assignment for pre-declared vars)
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: `match h.pet: case x:` pre-declares `std::variant<Cat, Dog> x;` (storage form) and assigns `x = __match_subject;` -- a deep copy of the Dog/Cat payload. CPython binds x to the same object, so later mutation through x is lost in TPy. Directly inconsistent with two siblings that get it right: a plain local assignment `x = h.pet` lowers to `std::variant<Cat*, Dog*> x = ::tpy::to_ptr_variant(h.pet)` (aliases), and `case Dog() as d:` binds `auto& d` (aliases). The divergence is parity-blind for read-only bodies, exactly the class the repo's test policy warns about.
- **Evidence**: Repro: match h.pet: case x: ... Generated: `std::variant<Cat, Dog> x;` then in default arm `x = __match_subject;` (copy). Contrast same file: `x = h.pet` generates `std::variant<Cat*, Dog*> x = ::tpy::to_ptr_variant(h.pet);` (alias); `case Dog() as d:` generates `auto& d = std::get<1>(__match_subject);` (alias).
- **Fix direction**: Bind reference-union captures in the borrow form (pointer variant via to_ptr_variant, like local assignment), pre-declaring as the ptr-variant type when the capture leaks the arm. Sibling: capture of a single-class union member and capture on pointer-repr Optional subjects should be audited for the same storage-form pre-declaration copy.
- **Verifier adjustment**: Codegen claim reproduced exactly: for `match h.pet: case x:` with pet: Cat|Dog, generated C++ is `std::variant<Cat, Dog> x;` (pre-declared via _emit_branch_decls, tpyc/codegen_cpp/statements.py:4718) then `x = __match_subject;` in the arm (match.py:998-999) -- a deep copy -- while the contrasts hold: `y = h.pet` emits `std::variant<Cat*, Dog*> y = ::tpy::to_ptr_variant(h.pet);` and `case Dog() as d:` emits `auto& d = std::get<1>(__match_subject);`. Runtime divergence reproduced end-to-end (bu...

#### G30. Match on IntEnum subject with int literal pattern crashes the compiler (assert in _group_switch_arms)

- **Location**: tpyc/codegen_cpp/match.py:792 (assert isinstance(pattern, TpyValuePattern) in _group_switch_arms kind='enum'), tpyc/cli.py:815-816 (prints 'Internal error: ' with empty message for AssertionError)
- **Severity / category**: medium / crash -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: CPython: `case 1:` on an IntEnum subject matches via == (Priority.Low == 1). TPy sema accepts the literal pattern (IntEnum-int comparison is allowed), but enum switch codegen asserts every arm is a TpyValuePattern and dies with a bare AssertionError, surfaced to the user as 'Internal error: ' with an empty message (AssertionError has no text and cli.py prints only str(e)). Either sema should reject literal patterns on enum subjects with a real diagnostic, or codegen should emit the underlying-value comparison.
- **Evidence**: Repro: class Priority(IntEnum): Low = 1; High = 2; def f(p: Priority): match p: case 1: ... case _: ... -> 'Internal error: ' (empty). With -v: AssertionError at tpyc/codegen_cpp/match.py:792 in _group_switch_arms.
- **Fix direction**: Decide the semantics (IntEnum literal arms are reasonable: emit `case static_cast<int32_t>(...)`-style or compare underlying values); at minimum convert the assert into a sema-phase rejection. Also fix cli.py to print the exception type when str(e) is empty.

#### G31. Optional[Optional[T]] is not collapsed -- nested std::optional, narrowing rejects valid code; inconsistent with `T | None | None`

- **Location**: tpyc/parse/type_resolver.py:400-411 (typing:Optional resolution wraps without flattening OptionalType inner)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: In Python typing, Optional[Optional[T]] IS Optional[T] (None is None; the runtime cannot distinguish layers). TPy keeps the nesting: the C++ param becomes std::optional<std::optional<int32_t>>, and `x is None` narrows only the outer layer, so `if x is None: ...; return x` under `-> Int32` is rejected with 'expected Int32, got Int32 | None'. The pipe spelling `Int32 | None | None` correctly collapses to std::optional<int32_t> (make_union dedups), so two spellings of the same Python type produce different TPy types -- a sibling inconsistency between the typing.Optional path and the union normalizer. Arises naturally via generics/aliases (Optional[X] where X is itself Optional).
- **Evidence**: Repro: def f(x: Optional[Optional[Int32]]) -> Int32: if x is None: return -1; return x -> 'error: Type mismatch in return value: expected Int32, got Int32 | None'. Codegen shape (narrow-free variant): `void f(std::optional<std::optional<int32_t>> x)`. Contrast: def f(x: Int32 | None | None) compiles to `std::optional<int32_t>` and narrows fine.
- **Fix direction**: Flatten at construction in type_resolver (if isinstance(inner, OptionalType): return inner), mirroring make_union's dedup. Sweep siblings: Optional[A | B | None], Optional[T] where T substitutes to an Optional via generic alias / type param.

#### G32. Union equality uses std::variant operator== (index-first): cross-alternative numeric equality diverges from CPython

- **Location**: generated `(a == b)` for UnionType operands; comparison sema/codegen for unions (no member-aware == lowering)
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: For a: Int32 | Float64, b: Int32 | Float64, TPy lowers `a == b` to std::variant operator==, which is False whenever the held alternatives differ. CPython compares the held VALUES: 1 == 1.0 is True. Reproduced: TPy prints False, CPython True. Silent wrong answer for any union mixing comparable members (int/float, bool/int, IntEnum/int). UNION_TYPES_DESIGN.md still lists Phase 9 'Equality ==/!= on unions' as 'Not designed', yet the operator is accepted and emitted -- the unimplemented design ships as variant== semantics with no diagnostic.
- **Evidence**: Repro: def f(a: Int32 | Float64, b: Int32 | Float64) -> bool: return a == b; print(f(1, 1.0)). Generated: `return (a == b);` over std::variant<int32_t, double>. TPy runtime output: False. CPython: True.
- **Fix direction**: Either implement Python-semantic union == (visit with cross-type numeric comparison for comparable member pairs) or reject == on unions whose member set contains cross-comparable types until Phase 9 is designed. Sibling: `in` membership and match literal arms over such unions inherit the same question.

#### G33. Literal patterns on union subjects with primitive members rejected; diagnostic leaks internal AST class name

- **Location**: tpyc/sema/match.py:649-653 (_analyze_pattern fall-through error 'Unsupported pattern type in match on union: TpyLiteralPattern')
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: CPython: `case 42:` on x: int | str matches the int alternative by equality. TPy rejects any literal pattern on a union subject. Workaround exists (case Int32() narrowing is also unsupported as a bare builtin pattern, so really: capture + guard), and the design doc flags this as a Phase-2 edge case -- but the diagnostic prints the internal class name 'TpyLiteralPattern' instead of user-facing wording, and gives no hint. Related sibling: `case Int32() as v:` / `case int():` (builtin class patterns) are rejected on Optional subjects too ('class pattern not valid for Optional inner type Int32') even though docs/MATCH_CASE_DESIGN.md:448-450 presents `case int() as v:` on an Optional subject as the canonical example -- doc/code divergence.
- **Evidence**: Repro: def f(x: Int32 | str): match x: case 42: ... -> 'error: Unsupported pattern type in match on union: TpyLiteralPattern'. Repro 2: match x (Optional[Int32]): case Int32() as v if v > 2: -> 'error: class pattern not valid for Optional inner type Int32' (the doc's own Optional example shape).
- **Fix direction**: Short-term: user-facing diagnostics with the guard workaround spelled out, and fix the doc example. Longer-term: support literal arms by narrowing to the literal's member type + value compare, and builtin-type class patterns (already listed as Future Extensions).

#### G34. Enum members named C++ keywords emitted unescaped -> raw C++ build failure (record fields escape, enums don't)

- **Location**: enum codegen (EnumUtil/enum-class emission; contrast escape via escape_cpp_name used for record fields)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: class Action(Enum): new = 0; delete = 1; default = 2 -- all valid Python identifiers -- generate `enum class Action { new = 0, delete = 1, default = 2 }` and `Action::new` references, which g++ rejects with raw keyword errors and no TPy diagnostic. The sibling path handles this: a record field named `new` is emitted as `new_` via escape_cpp_name. Enum member emission (enum class body, EnumUtil::name/from_value/members, member access codegen) bypasses the escaper.
- **Evidence**: Dump for the enum above: `case ::tpyapp::m23::Action::new: return "new";` / `Action::delete` / `Action::default` in EnumUtil and members array. Contrast record: `class R: new: Int32` generates `int32_t new_;`.
- **Fix direction**: Route enum member C++ identifiers through escape_cpp_name everywhere (enum class body, EnumUtil specialization, Color::Member accesses, match case labels), keeping .name strings and try_parse/from_name keys at the raw Python spelling.

#### G35. Generator finally (and with __exit__) never runs when the generator is abandoned mid-iteration

- **Location**: tpyc/codegen_cpp/gen_async.py (frame struct emission -- __finally_N helper declared but no destructor), tpyc/codegen_cpp/resumable_cfg.py:1190-1193 (finally helpers invoked only from iteration paths)
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: CPython runs a suspended generator's pending finally blocks (and with-statement __exit__) when the generator is closed or garbage-collected (GeneratorExit). TPy's generator frame struct has the finally body compiled into a `__finally_N()` helper invoked only on the normal-exhaustion and exception iteration paths; the struct has no destructor, so breaking out of a `for` loop (or otherwise dropping the frame) silently skips the cleanup. This breaks the idiomatic resource-cleanup-in-generator pattern (the basis of contextlib.contextmanager, which TODO.md plans to add). TODO.md tracks `close()` as an unimplemented API (line 335), but the silent skip of finally on implicit drop -- with no diagnostic -- is not recorded in BUGS.md.
- **Evidence**: Repro: def gen(): try: yield 1; yield 2 finally: print("cleanup"); consumer does `for x in gen(): print(x); break`. Observed run: `1 / after` -- no "cleanup" (CPython: `1 / cleanup / after`). Control: full exhaustion does print cleanup. Generated __gen_gen struct declares `void __finally_0();` but has no destructor invoking it.
- **Fix direction**: Emit a destructor (or close()) on the resumable frame that, when __state is a suspended-inside-try state, drives the pending finally helpers (the moral equivalent of throwing GeneratorExit). Needs care for finally bodies containing yield (CFG-based finally) and for with-regions (__exit__ must also run -- same gap). Sibling check for the gap sweep: async coroutine frames dropped without being polled to completion (outside the cancellation path) likely share the gap.

#### G36. A variable first bound inside a finally body generates non-compiling C++ (declared in catch-path copy, referenced undeclared in normal-path copy)

- **Location**: tpyc/codegen_cpp/statements.py:3732-3773 (_emit_try_with_finally duplication), 3494-3501 (_make_try_finally_emit reuses gen_stmt with persistent ctx.declared_vars)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: The finally body is emitted twice via gen_stmt (catch path, then normal path). gen_stmt's declared_vars tracking persists across the two emissions: the first copy (inside `catch (...) { }`) emits `std::string tmp = ...;`, the second copy then thinks tmp is declared and emits a bare assignment `tmp = ...;` at the outer scope, where the name does not exist. Any try/finally whose finally body introduces a new variable (e.g. `finally: elapsed = now() - start; print(elapsed)`) fails the C++ build with a confusing g++ error and no TPy diagnostic. Return-site inline emissions of the same finally body (_emit_finally_chain) would hit the same stale-declared_vars problem in reverse.
- **Evidence**: Repro: try: print("body") finally: tmp = "x" + "y"; print(tmp). Generated: `catch (...) { std::string tmp = ...; ... throw; } tmp = (::tpy::str_concat(...));`. Observed build: `error: 'tmp' was not declared in this scope; did you mean 'tm'?`.
- **Fix direction**: Either snapshot/restore ctx.declared_vars (and related local-scope state) around each duplicated finally emission so every copy declares its own locals in its own scope, or hoist finally-body locals like try-body vars are hoisted (sema _analyze_try_finally_only hoists try-body bindings but not finally-body bindings -- the asymmetry is the root). Also applies to the with-statement normal-exit frame if a future __exit__ inlining introduces locals.

#### G37. Bare re-raise of a return-tier error in a non-@error_return function emits `return make_unexpected(...)` from a void/plain function (invalid C++; missing sema check)

- **Location**: tpyc/sema/statements.py:1517-1530 (_analyze_raise bare-raise path checks only in_except_tier+binding), tpyc/codegen_cpp/statements.py:3363-3368 (_gen_raise unconditionally emits _make_return(make_unexpected))
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: Sema validates bare `raise` in a return-tier except handler only for the presence of an `as` binding -- it never checks that the ENCLOSING function is @error_return with a matching error type. Codegen then emits `return ::tpy::make_unexpected(std::move(*__err_opt_N));` regardless of the enclosing function's return shape. In a plain function (the natural place to log-and-propagate), this returns a value from `void main()` -- invalid C++, no TPy diagnostic. The existing test reraise_return_tier only covers the @error_return-enclosed case. Semantically, re-raising a return-tier error from a non-@error_return function should be a clean sema error (the error has nowhere to go) -- mirroring the doc's enforcement table (EXCEPTION_DESIGN.md:185-195).
- **Evidence**: Repro: def main() -> None: try: x = find(False) except NotFound as e: print("nope"); raise. Generated inside void main(): `return ::tpy::make_unexpected(std::move(*__err_opt_1));` -- g++ rejects (return value in void function).
- **Fix direction**: In _analyze_raise's bare-raise return-tier branch, require func.error_return to be set and error_return_matches(func.error_return, handler's E); otherwise emit a diagnostic like "bare 'raise' of return-tier 'NotFound' requires @error_return(NotFound) on the enclosing function". Sibling check: methods and nested defs use the same path; also verify @error_return(F) enclosing with E != F is rejected.

#### G38. @error_return unwrap sites copy-assign instead of moving: @nocopy success types (Box/Rc) break the C++ build; reference types silently copy the returned value

- **Location**: tpyc/codegen_cpp/statements.py:4053, 4078, 4112, 4130, 4159, 4175 (all `= ::tpy::unwrap_ref(*tmp);` with no move), 4014-4017 (_gen_propagate_check copies `.error()` on the no-finally path)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: Statement-level @error_return handling (try-dispatch, auto-propagate, top-level unwrap, for both var-decls and assignments) extracts the success value with `name = ::tpy::unwrap_ref(*tmp);` -- a copy from the expected's lvalue, not a move. Consequences: (1) an @error_return function returning Own[Box[T]] / Own[Rc[T]] (the canonical owning types, both @nocopy) consumed inside try/except produces non-compiling C++ (deleted copy-assign into std::optional<Box<int>>) with no TPy diagnostic -- valid combination of two flagship features rejected via g++ error spew; (2) for copyable reference types (list/dict/class) the freshly returned value is deep-copied where a move is always correct (perf pessimization, contradicts the zero-cost goal). Note the expression-level unwrap path already got this right (`unwrap_ref_move` in the statement-expression form, and the expr_unwrap_nocopy test) -- the statement-level paths were not updated, a sibling inconsistency.
- **Evidence**: Repro: @error_return(NotFound) def find(flag: bool) -> Own[Box[Int32]] consumed via `b = find(True)` in try/except. Build fails with optional<Box<int>>::operator= no-match errors (Box copy deleted). Copy evidence: er_nodefault2 repro generates `p = ::tpy::unwrap_ref(*__try_tmp_2);` for an Own[Point] return -- copy, no std::move. Propagate path: `return ::tpy::make_unexpected({tmp}.error());` copies the error object.
- **Fix direction**: Use the move form at all six statement-level unwrap sites (`::tpy::unwrap_ref_move(*tmp)` / `std::move(tmp.error())`), matching the expression-level path and _gen_error_goto's `std::move(tmp.error())`.

#### G39. Resumable with A(), B(): all __enter__ calls emitted unprotected in S_INITIAL -- exception in B.__enter__ skips A.__exit__ (async-function sibling of the sync _gen_with finding)

- **Location**: tpyc/codegen_cpp/resumable_cfg.py:1400-1431, tpyc/codegen_cpp/gen_async.py (case emit of WithEnter)
- **Severity / category**: medium / miscompile -- reproduced: yes -- found by `gap-siblings`
- **Problem**: Round 1 found sync _gen_with emits all manager constructions + __enter__ calls flat, so an exception in B.__enter__ skips A.__exit__. The CFG path used when the with-body contains an await has the same flaw at a different site: CFGBuilder._build_with appends a WithEnter for EVERY item into the current (pre-region) basic block (lines 1405-1415); the WithRegion try/catch wrapping applies only to the body BBs built afterwards. So in the emitted poll(), case S_INITIAL runs `ctx0.emplace(...); ctx0.__enter__(); ctx1.emplace(...); ctx1.__enter__();` with no try/catch at all -- if the second manager's __enter__ (or constructor) throws, the first manager's __exit__ never runs. Python semantics: `with a, b:` == `with a: with b:`, so a's __exit__ must run.
- **Evidence**: Repro /tmp/agents/sib/t11_async_with.py: async def caller with `with Tracer("A", False), Tracer("B", True):` (B.__enter__ raises) and an await in the body. Generated __coro_caller::__poll__: `case S_INITIAL: { __with_ctx_0.emplace(Tracer("A", false)); (*__with_ctx_0).__enter__(); __with_ctx_1.emplace(Tracer("B", true)); (*__with_ctx_1).__enter__(); __state = S_JOIN_0; continue; }` -- the only try/catch pairs that call __exit__ live in case S_RESUME_0 (the body BB). 'exit A' is never printed; CPython prints it.
- **Fix direction**: Wrap each subsequent item's WithEnter in the already-pushed outer WithRegions (start a fresh BB after each region push so region membership covers later enters), mirroring the fix for the sync path. Note the async-with path (_build_async_with, items recursed with each item's __aenter__ inside the previous item's TryRegion per the docstring at 1438-1443) appears correctly nested; only the sync-managers-in-async-function path is flat.

#### G40. dict literal storing a reference-type lvalue copies with no warning (append warns; sibling of the round-1 list-literal finding)

- **Location**: tpyc/sema/expressions.py:2705-2814 (_analyze_dict_literal)
- **Severity / category**: medium / silent-copy -- reproduced: yes -- found by `gap-siblings`
- **Problem**: OWNERSHIP_DESIGN rule 2 promises a copy warning whenever a pointer-variable is stored into container storage; lst.append(p) warns. Round 1 found list literals `[p]` lack the warning; the dict-literal value position is a sibling gap: `d = {1: p}` deep-copies p into the ordered_map with no diagnostic, so subsequent mutations of p are invisible through d (CPython aliases). _analyze_dict_literal has no borrowed-source copy-warning path at all (the warning logic lives in the append/Own-param coercion in compatibility.py and was never added to any literal form).
- **Evidence**: Repro /tmp/agents/sib/t3c.py: `p = P(); d = {1: p}; s = [p]; s.append(p); p.v = 5`. Diagnostics: only one warning, for the append line (`t3c.py:11: warning: copies P into owned storage; use copy() to make this explicit`). Generated C++: `::tpy::ordered_map<int32_t, P> d = ::tpy::ordered_map<int32_t, P>({{1, p}});` -- copy-constructs p; `d[1].v` prints 0, CPython prints 5.
- **Fix direction**: Apply the same borrowed-source-into-owned-storage warning used by append/Own coercion to dict-literal values (and keys), in the same change that fixes the round-1 list-literal sibling. Set literals and dict comprehension values untested -- likely the same gap; flag for gap sweep.

#### G41. readonly containers rejected by sum/sorted/zip stubs (sibling cluster of the round-1 enumerate finding); max/min have no iterable overload at all

- **Location**: lib/tpy/tpy/_builtins/_funcs.py:455-509 (sum), lib/tpy/tpy/_builtins/_funcs.py:519-525 (sorted), lib/tpy/tpy/_builtins/_funcs.py:597-612 (zip), lib/tpy/tpy/_builtins/_funcs.py:125-180 (min/max)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-siblings`
- **Problem**: Round 1 found enumerate() rejects readonly containers because its stub takes mutable Iterable[T]. The same flaw exists in the sibling stubs: sum(), sorted(), and zip() all declare `iterable: Iterable[T]` without readonly, so pure-read aggregation over a readonly[list[Int32]] param is impossible (`Cannot pass readonly[list[Int32]] as mutable Iterable[Int32]`). reversed() (Sequence[T]) and any/all over a genexpr work, making the inconsistency surprising. Additionally min/max have NO Iterable overload at all -- `max(xs)` on a plain mutable list[Int32] is 'No matching overload' (only 2/3-arg scalar and key forms exist, lines 125-180), an unconditional rejects-valid on one of the most common Python idioms.
- **Evidence**: Repros /tmp/agents/sib/t4b.py `def f3(xs: readonly[list[Int32]]) -> Int32: return sum(xs)` -> 'error: Cannot pass readonly[list[Int32]] as mutable Iterable[Int32] in argument iterable'; t4d.py sorted(xs) -> same; t4_readonly_iters.py zip(xs, ys) -> same on argument 'iter1'; t4f.py `max(xs)` on mutable list[Int32] -> 'No matching overload for max(list[Int32])'. t4a.py reversed(xs) on readonly compiles clean.
- **Fix direction**: Annotate the iterable params of sum/sorted/zip (and enumerate, per round 1) as readonly[Iterable[T]] (or whatever spelling the stdlib uses for read-only iteration), and add min/max Iterable[T] overloads. One stub-file change, same root cause as the round-1 enumerate entry.
- **Verifier adjustment**: The readonly-rejection cluster is confirmed by repro: sum(xs) on readonly[list[Int32]] -> 'Cannot pass readonly[list[Int32]] as mutable Iterable[Int32] in argument iterable'; sorted(xs) same; zip(xs, ys) same on 'iter1'; reversed(xs) on readonly compiles clean (inconsistency confirmed). Stubs at lib/tpy/tpy/_builtins/_funcs.py:455-509 (sum), 514-525 (sorted), 594-612 (zip) all take mutable Iterable[T]. ADJUSTMENT: the min/max half is NOT a new discovery -- `max(xs)` -> 'No matching overload f...

#### G42. Empty list/dict literal under an Optional/Union annotation rejected: hint unwrap stops at Readonly/Own (decl, call-arg, both)

- **Location**: tpyc/sema/expressions.py:425-441 (empty-literal hint match), tpyc/sema/expressions.py:548-561 (dict-literal hint dispatch)
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-siblings`
- **Problem**: Sibling of the round-1 nested-container hint-propagation findings, on the Optional/Union axis. The empty-list-literal hint matcher unwraps only ReadonlyType and OwnType from the hint (430-432) before testing `is_list(inner_hint)` (441), so a hint of `list[Int32] | None` never matches: `x: list[Int32] | None = []` is rejected ('[] requires matching type annotation, got list[Int32] | None') and passing `[]` to a `xs: list[Int32] | None` parameter fails with the internal-type-leaking 'expected list[Int32], got PendingList[???, 0]#0'. The empty dict literal has the same failure (`d: dict[str, Int32] | None = {}` -> 'Cannot infer types for dict'). Non-empty literals into the same Optional param work, making the empty case a surprising cliff. CPython idiom 'default to empty, maybe None later' is mainstream.
- **Evidence**: Repros: /tmp/agents/sib/tD4.py `x: list[Int32] | None = []` -> 'error: [] requires matching type annotation, got list[Int32] | None'; tD2_empty_list.py `def f(xs: list[Int32] | None)...; f([])` -> 'error: Type mismatch in argument xs: expected list[Int32], got PendingList[???, 0]#0' while `g(xs: list[Int32]); g([])` and `f([1, 2])` compile clean; tD5.py `d: dict[str, Int32] | None = {}` -> 'Cannot infer types for dict d'. Code: expressions.py:428-441 -- `inner_hint = unwrap_readonly(type_hint); if isinstance(inner_hint, OwnType): inner_hint = inner_hint.wrapped; ... hint_matches = is_list(inner_hint)` -- no OptionalType/UnionType member search, unlike the recursive-union path for non-empty literals at 497-509.
- **Fix direction**: When the (Readonly/Own-unwrapped) hint is an Optional/Union, search it for a single list/dict/set member and use that as the literal's hint (same _find_list_member/_find_dict_member machinery already present for recursive-union wrappers at 497-509 and 556-561). Set-literal empty form (`set()` constructor) untested -- trace in gap sweep.

#### G45. Pinned StrView alias warning fires on reassignment but not augmented assignment of the source -> silent dangle

- **Location**: tpyc/sema/statements.py:4142,3427 (call sites) vs 4601-4692 (_analyze_aug_assign, no _handle_pinned_view_rebind call)
- **Severity / category**: medium / unsound-safety -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: `c: StrView = a` registers a pinned-view alias (statements.py:3491-3499) so that REASSIGNING `a` warns ('reassignment invalidates view'). But `a += ...` -- which reallocates the std::string buffer just as surely -- never calls _handle_pinned_view_rebind: the aug-assign path only calls mark_all_view_borrowers_mutated (which helps pending views, not pinned ones, since pinned views were never ViewVarInfo-registered). Result: explicit StrView alias + source aug-assign compiles silently and dangles. Also, the pinned-alias registration only handles init from a bare TpyName, so `c: StrView = a[2:8]` registers nothing at all.
- **Evidence**: Repro: a = make(); c: StrView = a; a += ' mutated...' -> emits `std::string_view c = a; a += "...";` with no warning (dump shows no diagnostic output); c dangles after the +=. Same program with plain `b = a` correctly promotes b to std::string.
- **Fix direction**: Call _handle_pinned_view_rebind from _analyze_aug_assign for name targets (aug-assign is a rebind for buffer purposes); extend pinned-alias registration to subscript/field-rooted inits using _borrow_storage_root. Note BorrowTracker direction: this should become a LoanInfo on the place, not another string-key dict.

#### G46. StrView record field accepts any source; constructing from a local and returning the object dangles with no diagnostic (doc-acknowledged 'Future')

- **Location**: tpyc/sema/statements.py (field assignment path -- no source check), docs/STRING_HANDLING.md:235-243
- **Severity / category**: medium / unsound-safety -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: A `v: StrView` field stores a non-owning view inline. Assigning a str param to it in __init__ and returning the object via Own[C] yields a field viewing a destroyed local. STRING_HANDLING.md explicitly lists 'str field lifetime safety rules' as Future and sketches allowed/disallowed sources, so this is a known gap -- reported here for inventory completeness because the failure is fully silent and the construct (store a string on an object) is the single most common escape shape.
- **Evidence**: Repro: class C: v: StrView; __init__ stores `self.v = s` (s: str param); `def build() -> Own[C]: local = make(); return C(local)` emits `C build() { std::string local = make(); return C(local); }` with `struct C { std::string_view v; ... }` -- c.v in main() views the dead `local`. No diagnostic.
- **Fix direction**: Implement the documented conservative source rules (allow literals/Final/global/owned-field-of-same-object; reject params, locals, call results) at field-assignment coercion. Must cover __init__, ordinary methods, and direct `obj.v = ...` writes uniformly (functions vs methods vs constructors sibling rule).

#### G47. Every str-keyed dict/set lookup with a view key allocates a temporary std::string (non-transparent hash/compare)

- **Location**: runtime/cpp/include/tpy/ordered_map.hpp:361,213,274,278, runtime/cpp/include/tpy/dict_ops.hpp:67-110
- **Severity / category**: medium / perf -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: ordered_map's backing table is `std::unordered_map<K, Node*>` with default (non-transparent) hash/equal, and every lookup helper materializes the key: dict_get does `m.find(K(key))`, contains/erase/operator[] take `const K&`. Since TPy's entire string design pushes locals and params to std::string_view, the dominant key shape at call sites is a view -- so every `d[k]`, `k in d`, `d.get(k)`, `del d[k]` on dict[str, V] constructs (and for >SSO keys heap-allocates) a std::string per operation. For a language whose stated goal is low-latency hot paths, str-keyed dict access is a core hot-path primitive. Same applies to ordered_set<std::string>.
- **Evidence**: Generated code: `::tpy::__getitem__(d, k)` with `std::string_view k` against `::tpy::ordered_map<std::string, int32_t>`; dict_ops.hpp:69-72 `V* dict_get(...){ auto it = m.find(K(key)); ... }` -- K(key) copies. ordered_map.hpp:361 `std::unordered_map<K, Node*> table_;` -- no transparent hasher, so even a heterogeneous find would not compile today.
- **Fix direction**: Give ordered_map/ordered_set a transparent hasher/equal (std::hash<std::string_view>-based with is_transparent) and templated lookup overloads; drop the K(key) materialization in dict_ops helpers for lookups (keep it for inserts).
- **Verifier adjustment**: Perf claim confirmed: ordered_map.hpp:361 `std::unordered_map<K, Node*> table_` with default non-transparent hash; dunder.hpp:166,173 `__getitem__` does `m.find(K(key))`; dunder.hpp:245 `__delitem__` does `m.erase(K(key))`; dict_ops.hpp:70,78,86,98,108 (dict_get/dict_pop/dict_get_default) all do `K(key)` -- each lookup with a string_view key constructs a std::string (heap alloc beyond SSO). Repro confirms TPy emits view-typed key locals (`std::string_view k = "a"`) against `ordered_map<std::s...

#### G48. str.encode('utf-8') / bytes.decode('utf-8') rejected -- stubs accept no codec argument

- **Location**: lib/tpy/tpy/_builtins/_types.py:625, lib/tpy/tpy/_builtins/_bytes.py:111,307
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: encode()/decode() exist only as zero-arg stubs, so the most common explicit form in real Python code -- s.encode('utf-8'), b.decode('utf-8'), often with errors='...' -- fails with "'encode' expects 0 argument(s), got 1". Since TPy strings are raw UTF-8 bytes anyway, accepting (at least) the literal 'utf-8'/'ascii' codec argument is semantically free and removes a pervasive porting papercut.
- **Evidence**: Repro: u = 'abc'.encode('utf-8') -> error: 'encode' expects 0 argument(s), got 1. Zero-arg forms work: `::tpy::bytes_from_str("abc")` / `::tpy::bytes_decode(c)`.
- **Fix direction**: Add overloads taking `encoding: str` (compile-time-validated literal 'utf-8'/'ascii'; reject others with a clear message), optionally `errors`. Keep zero-arg fast path.

#### G49. bytes cannot appear in an f-string though print(bytes) works -- inconsistent sibling support

- **Location**: tpyc/sema/expressions.py:3601 (f-string formattability check), runtime BytesPrinter in printing path
- **Severity / category**: medium / rejects-valid -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: f"{b}" where b: bytes errors 'Type bytes cannot be used in f-string (no __str__ or __repr__ method)', yet `print(b)` compiles and prints via ::tpy::BytesPrinter. CPython renders f"{b}" as b'...'. The runtime already knows how to render bytes; only the f-string formattability whitelist excludes it. Same presumably applies to bytearray/BytesView.
- **Evidence**: Repro: b = b'abc'; print(f'{b}') -> error: Type bytes cannot be used in f-string (no __str__ or __repr__ method). Replacing with print(b) emits `std::cout << ::tpy::BytesPrinter(b)` and compiles.
- **Fix direction**: Route bytes/bytearray/BytesView f-string parts through the existing BytesPrinter/repr helper (matching CPython's b'...' rendering) in _gen_fstring's per-type wrapping table, and whitelist them in the sema formattability check.

#### G50. Byte-based string semantics (len/indexing/slicing on UTF-8 bytes, not code points) are an undocumented Python divergence; no doc or diagnostic

- **Location**: docs/STRING_HANDLING.md (silent), docs/LANGUAGE_FEATURES.md (silent), TODO.md:414
- **Severity / category**: medium / design -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: len(s), s[i], s[i:j], the Char type, isalpha/upper/lower (ASCII-only, partially documented) all operate on UTF-8 BYTES while CPython operates on code points. Nothing in STRING_HANDLING.md or LANGUAGE_FEATURES.md states this contract; TODO.md:414 ('c++ generation profiles: utf8 strings vs char strings') hints the design question is known but unresolved. Any non-ASCII data makes len/index/slice silently diverge from CPython (and the cpy test phase would catch it only if a test ever used non-ASCII). The string_dispatch miscompile (separate finding) is one internal consequence of the same ambiguity -- the compiler itself disagreed with its own runtime about what len(s) means.
- **Evidence**: len('zołw-with-diacritics') compiles to string_view::size() (bytes); Python returns code-point count. s[0] on a non-ASCII first char yields the first UTF-8 byte as Char. No doc section defines this; the compiler internally used Python len(s) for codegen decisions (see match-dispatch finding) proving the contract is not pinned down even inside the project.
- **Fix direction**: Write the byte-semantics contract into LANGUAGE_FEATURES.md/STRING_HANDLING.md explicitly (HFT-internal audience can accept byte semantics, but it must be stated), and audit compiler-side uses of Python len()/ord() on user string literals for code-point/byte confusion.

#### G51. Top sema/codegen mirroring-drift risks between calls.py and codegen expressions.py dispatch structures

- **Location**: tpyc/codegen_cpp/expressions.py:2536-2551, tpyc/codegen_cpp/expressions.py:2776-2806, tpyc/codegen_cpp/expressions.py:2781-2786, tpyc/codegen_cpp/expressions.py:962-1104, tpyc/sema/calls.py:4685, tpyc/sema/calls.py:4713, tpyc/sema/calls.py:3415
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `gap-bigfiles-second-pass`
- **Problem**: Requested drift inventory from the fresh-eyes pass; the two confirmed build-failure findings above are instances of items 1 and 2. (1) Error-return context model: sema's _check_error_return_handled accepts any lexical try/except or @error_return caller, while codegen's _maybe_error_return_unwrap realizes 'handled' as function-local gotos/returns -- every codegen construct that introduces a C++ lambda boundary (genexpr, simple-generator peephole, nested defs, Callable-context lambdas) silently invalidates the contract. (2) Generic-call ABI: sema infers type_subst against declared storage-form types and records no coercions, while codegen independently re-substitutes (expressions.py:2802) and instantiates with type_to_cpp_stored -- the borrow/storage decision is made twice with no shared fact, and disagrees for unions/pointer-repr Optionals. (3) Overload-stub choice: _gen_call picks `func_infos[0]` vs `expr.resolved_function_info` based on `len(func_infos) > 1` with an inline caveat that resolved stubs 'break is_generic() detection' -- codegen is re-deriving which FunctionInfo sema meant instead of sema stamping the emission stub on the node (the recent generic-overload link fix at commit 84539a714 patched one symptom of this). (4) gen_call_arg's conversion cascade (_gen_dynamic_protocol_arg -> _gen_covariant_arg -> _gen_protocol_arg -> _gen_optional_ptr_arg -> _gen_union_arg -> temp-materialization, expressions.py:2820-2872) re-derives from ptype shape what sema's check_type_compatible/coerce_expr already decided per arg; the same cascade is duplicated with different subsets in the constructor path (expressions.py:2962-3010) and the method path -- each new arg-shape rule must be added in 3+ places. (5) resolve_kwargs mutates expr.args destructively (calls.py:607-608) and stamps gap-filled default expressions via shallow dc_replace, so the same default child nodes are re-analyzed at every call site -- combined with analyze_expr's known non-idempotence this is a latent id(node)-keyed-fact aliasing hazard. All five are exactly the consumer-side dispatch CLAUDE.md's THIR notes warn about.
- **Evidence**: expressions.py:2783-2786: 'Note: don't use resolved_function_info for generic functions because it has substituted types (breaks is_generic() detection)'. expressions.py:2553-2555 unwrap comment assumes statement context: 'The pointer survives the scope'. calls.py:607: 'expr.args = resolve_kwargs(...); expr.kwargs = {}'. Findings 2 and 3 in this report are reproduced consequences of drift items 1 and 2.
- **Fix direction**: Per the repo's own THIR guidance: stamp the chosen emission FunctionInfo, the per-arg conversion verdict (borrow->storage kind), and the error-return handling mode (label / propagate / panic / lambda-barrier) on the call node in sema, and make codegen a pure renderer of those facts.

#### G52. Starred unpack into a fixed-arity call reports a misleading arity error instead of the dedicated *args diagnostic

- **Location**: tpyc/sema/calls.py:4729-4734 (generic-path arity check counts TpyStarUnpack as one arg), tpyc/sema/calls.py:4536 and tpyc/sema/calls.py:4839 (the real 'Cannot use *unpacking' diagnostics, reached only when arity happens to pass)
- **Severity / category**: low / slop -- reproduced: yes -- found by `gap-bigfiles-second-pass`
- **Problem**: `consume(*args)` against a 2-param function fails with "'consume' expects 2 argument(s), got 1" -- the arity check runs before star-unpack validation and counts the TpyStarUnpack node as a single argument, so the user is told to add an argument rather than that *unpacking is unsupported for fixed-arity callees. When arity accidentally matches (1 starred arg, 1 param) the correct diagnostic fires: "Cannot use *unpacking: 'g' does not accept *args". Diagnostic-order inconsistency only; no miscompile (Python's `f(*t)` for fixed arity is a missing feature with an explicit rejection).
- **Evidence**: /tmp/agents/x2/c5_star_own.py (2 params): `error: 'consume' expects 2 argument(s), got 1`. /tmp/agents/x2/c40_star_arity1.py (1 param): `error: Cannot use *unpacking: 'g' does not accept *args`.
- **Fix direction**: Check for TpyStarUnpack args before the arity check in _analyze_single_function_call/_analyze_generic_function_call and emit the dedicated diagnostic (or implement static tuple expansion, which would also subsume the Own[T]-through-star case -- untested interaction flagged for any future implementation).

#### G53. Match/isinstance narrowing-related notes: subject narrowing makes genuinely-dead arms a hard error; reassignment does not re-narrow

- **Location**: tpyc/sema/match.py:221-246 (subject narrowing + unreachable-arm rejection), tests/cases/union/union_assign_narrowing_reassign (pins reassignment losing narrowing)
- **Severity / category**: low / design -- reproduced: yes -- found by `gap-enum-union-match`
- **Problem**: Two small ergonomic asymmetries observed while probing. (1) Assignment narrowing (s: Dog|Cat = Dog(...)) narrows the match subject to the concrete record, which flips the match into record field-value mode where the first all-capture arm acts as a wildcard -- a following `case Cat():` arm is then rejected as 'unreachable case after wildcard pattern'. The code is genuinely dead, but the error message talks about a wildcard the user never wrote and CPython accepts the code; a 'subject is known to be Dog here' phrasing (or a warning) would explain the rejection. (2) Reassigning a union local (`s = Cat(9)`) clears narrowing but does not re-narrow from the RHS, unlike the declaration initializer -- pinned as intended by union_assign_narrowing_reassign, but it is an inconsistency between declaration and reassignment a user will trip on (`s = Cat(9); print(s.lives)` errors).
- **Evidence**: Repro 1: s: Dog | Cat = Dog('rex'); match s: case Dog(name=n): ... case Cat(): ... -> 'error: unreachable case after wildcard pattern' (the Cat arm; no wildcard in source). Repro 2: s = make(True); s = Cat(9); print(s.lives) -> 'error: Cannot access field lives on type Cat | Dog'.
- **Fix direction**: Improve the diagnostic for narrowed-subject matches to name the proven concrete type; consider RHS re-narrowing on reassignment for symmetry with declarations (mypy does). Both are sema-only.

#### G54. `except (A, B)` tuple form rejected

- **Location**: tpyc/parse/parser.py:3328-3332
- **Severity / category**: low / rejects-valid -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: The standard Python multi-type handler `except (ValueError, TypeError):` is rejected at parse time (clean diagnostic: "'except' requires a simple or dotted name"). The workaround -- two handlers -- duplicates the handler body, and there is no way to bind one name over two types. Not listed in EXCEPTION_DESIGN.md's supported-forms table nor as a future extension, so the gap is undocumented.
- **Evidence**: Repro tuple_except.py: `except (ValueError, TypeError):` -> error: 'except' requires a simple or dotted name (e.g. 'except MyError' or 'except pkg.MyError').
- **Fix direction**: Throw-tier lowering is trivial: emit consecutive catch clauses sharing an emitted handler-body lambda, or catch the nearest common base and dispatch. At minimum, document the limitation in EXCEPTION_DESIGN.md/LANGUAGE_FEATURES.md.

#### G56. Module-level `raise` rejected ("can only be used inside a function")

- **Location**: tpyc/sema/statements.py:1537-1540
- **Severity / category**: low / rejects-valid -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: _analyze_raise requires ctx.func.current_function to be a TpyFunction, so a top-level `raise ValueError(...)` -- valid Python, and the natural shape for module init guards -- is rejected. Module-level try/except/finally around a raising CALL works fine (verified), so the gap is only the direct raise statement; clean diagnostic, easy workaround (wrap in a function).
- **Evidence**: toplevel_try.py with a direct top-level raise: `error: 'raise ValueError' can only be used inside a function`. Control toplevel_try2.py (top-level try/except/finally around boom()) builds and prints `caught x / fin` correctly.
- **Fix direction**: Allow throw-tier raise in the top-level/__tpy_init context (return-tier raise legitimately needs a function). The function check should distinguish tiers rather than blanket-reject.

#### G57. sema ctx.in_finally is write-only dead state

- **Location**: tpyc/sema/context.py:817, tpyc/sema/statements.py:1749-1753, 1854-1858, 1974-1978
- **Severity / category**: low / slop -- reproduced: yes -- found by `gap-exceptions-with`
- **Problem**: ctx.in_finally is set/restored around all three finally-body analysis sites but is never read anywhere in tpyc (grep confirms only the declaration and the three save/set/restore triples). Either a planned check (e.g. warning on return/break in finally swallowing exceptions) was never wired, or it is leftover. Dead per-statement bookkeeping is exactly the kind of state the THIR migration guidance says not to accumulate.
- **Evidence**: grep -rn '\.in_finally' tpyc/ -> only sema/statements.py writes (1749-1753, 1854-1858, 1974-1978) and the context.py:817 field; zero reads.
- **Fix direction**: Delete the field and the three save/restore triples, or wire the intended consumer.

#### G58. Doc rot and self-contradictions in STRING_HANDLING.md / FSTR docs / deduction comments

- **Location**: docs/STRING_HANDLING.md:87,235-243, docs/STRING_HANDLING.md:27-28 vs behavior, tpyc/sema/local_deduction.py:850-852 vs 866-870, docs/FSTR_DESIGN.md:44-45
- **Severity / category**: low / slop -- reproduced: yes -- found by `gap-strings-bytes`
- **Problem**: (a) STRING_HANDLING.md's type table says a `str` class field is `std::string` ('Owned -- field outlives any source'), which matches actual codegen, but the 'Future: str Field Lifetime Safety' section (235-243) asserts '`str` fields store a `string_view` inline' -- describing StrView fields, not str fields; the section contradicts the table. (b) The roadmap lists 'f-string Optional[T] support (narrowed optional in f-string context)' as Planned, but narrowed Optional[str] in an f-string works today (emits std::format("o={}", (*o))). (c) FSTR_DESIGN/STRING_HANDLING say `=` is rejected, but the debug specifier f"{x=}" compiles and emits repr_of -- the docs conflate `=` alignment with the debug form. (d) is_view_compatible_source's docstring says 'bytes literals are NOT view-safe (temporary vectors...)' while the body 15 lines later returns True for TpyBytesLiteral with a comment explaining they ARE view-safe via static arrays.
- **Evidence**: STRING_HANDLING.md:87 '| Class field | std::string | Owned...' vs :237 'str fields store a string_view inline -- a non-owning view'. Dump of `o: Optional[str]` narrowed in f-string: `std::format("o={}", (*o))` compiles fine. Dump of f'{x=}': `std::format("x={}", ::tpy::repr_of(x))`. local_deduction.py:850-852 vs 866-870 contradictory comments.
- **Fix direction**: Fix the Future section to talk about StrView fields; update roadmap rows that are already Done; split '= alignment' from '= debug specifier' in the docs; reconcile the bytes-literal comment pair.
- **Verifier adjustment**: 3 of 4 sub-claims hold; sub-claim (c) is refuted. (a) CONFIRMED: STRING_HANDLING.md:87 says class-field str is std::string ('Owned -- field outlives any source'), but :237 in the Future section says 'str fields store a string_view inline -- a non-owning view' -- direct contradiction; the section describes StrView fields (and the codegen-verified behavior matches the :87 table: str fields are owned). (b) CONFIRMED: roadmap row :28 lists 'f-string Optional[T] support (narrowed optional in f-str...

**Round-2 re-discoveries (independent cross-confirmation of entries above):**

- "Optional-subject match: wildcard/capture arms exclude None -> std::unreachable / stack smashing" (`gap-enum-union-match`) -- duplicate of round-1 Optional-match wildcard/None UB (theme: match)
- "Multi-manager `with a, b:` skips a.__exit__ when b's constructor or __enter__ raises" (`gap-exceptions-with`) -- duplicate of round-1 `with a, b:` __exit__ skip (theme: exceptions/with)
- "String match/case switch dispatch confuses Python code points with C++ bytes: non-ASCII case literals silently never match (miscompile, runtime-confirmed twice)" (`gap-strings-bytes`) -- duplicate of round-1 string-switch non-ASCII miscompile (theme: expression-level)
- "Enum member names not escaped: `new = 1` emits `enum class E { new = 1, ... }` -- ill-formed C++ (sibling of the escape_cpp_name cluster)" (`gap-siblings`) -- same bug as the gap finding on enum members named after C++ keywords; merged there
- "bytearray local alias silently copies (CPython aliases) -- violates the reference-type discipline; list aliases correctly" (`gap-strings-bytes`) -- manifestation of round-1 bytearray-is-value-type (theme: silent copies)
- "`raise X from Y` silently drops the cause (no diagnostic)" (`gap-exceptions-with`) -- duplicate of round-1 raise-from drop (theme: parser fidelity)


## 6. Design concerns

#### D141. populate_const_borrow_params ABI facts diverge from emitted const-method signatures

- **Location**: tpyc/codegen_cpp/param_const.py:144-184, tpyc/codegen_cpp/functions.py:1529-1568, tpyc/codegen_cpp/functions.py:713-734
- **Severity / category**: medium / design -- reproduced: no (code-read evidence) -- found by `codegen-funcs-records`
- **Problem**: The module's stated goal is that the const decision is computed once and 'call sites read it directly without re-deriving'. But the signature emitter for const methods uses const_params=True with gmp (_get_method_genuine_mutated_params: direct Phase-1 mutations minus return_borrows_from), while populate_const_borrow_params calls decide_param_const with const_params=False and the full transitively-propagated fi.mutated_params. For a @readonly method whose param is transitively-marked or return-borrow-marked mutated, the emitted signature is const while fi.const_borrow_params says non-const (and vice-versa for no-mutable-borrow-surface or TypeParamRef params under forced const, where the signature is const but the FI fact is not). Any consumer of fi.const_borrow_params/deep_const_borrow_params for call-arg lowering of deep-const shapes (tuple<const T*> vs tuple<T*>, const ptr-variant) can then disagree with the actual parameter type -- variant<A*,B*> notably does not convert to variant<const A*, const B*>. Not reproduced end-to-end; filing as the exact kind of re-derivation drift param_const.py was created to eliminate, and a THIR-migration hazard (two sources of truth for one ABI fact).
- **Evidence**: param_const.py:168-177 passes `mutated_params=fi.mutated_params, ... use_readonly_params=fi.is_readonly` but never const_params; functions.py:1537 computes `gmp = self._get_method_genuine_mutated_params(...) if use_const_params else None` and 1555-1560 emits the signature from gmp with const_params=True. decide_param_const(const_params=True, directly_mutated=False) returns const for params that the populate path classifies _NOT_CONST (e.g. inner TypeParamRef hits the line-128 early return; transitive mutated hits line 126).
- **Fix direction**: Pass the same inputs in both places: populate should use const_params=fi.is_readonly (or store gmp on FunctionInfo and use it in both), so the frozen ABI fact and the emitted signature are computed from one rule. Audit the constructors' const_params=True path (records.py:417-443) for the same divergence.

#### D142. List slicing (including a[:], Python's canonical copy idiom) yields a writable alias view with no warning

- **Location**: docs/LANGUAGE_FEATURES.md:634, docs/LANGUAGE_FEATURES.md:5572 (documented design); slicing lowering in tpyc (lst[x:y] -> Span[T])
- **Severity / category**: medium / design -- reproduced: yes -- found by `hunt-silent-copy`
- **Problem**: `b = a[0:2]` and `b = a[:]` produce Span[T] views; `b[0] = 99` writes through to `a`. CPython list slicing COPIES, and `a[:]` is the idiomatic spelling of 'give me a copy'. This is the only divergence found that goes in the aliasing direction (every other divergence copies-where-Python-aliases and warns); it is silent -- no warning at the view creation or the write-through. The design itself (zero-copy views, HFT goal) is documented and defensible for `a[i:j]`, but `a[:]` specifically inverts the universally-understood copy idiom into full aliasing, a trap for any ported Python code (b = a[:]; b.sort()-style defensive copies silently mutate the original where the op is available on Span, and reject otherwise).
- **Evidence**: Repro: a: list[Int32] = [1, 2, 3]; b = a[:]; b[0] = 99; print(a[0]) -> TPy prints 99, CPython prints 1. Same for b = a[0:2]. No diagnostic. Docs: 'lst[x:y] basic slicing -> Span[T] (zero-copy view...)' (LANGUAGE_FEATURES.md:634).
- **Fix direction**: Language-design call for the user: either special-case the full-slice form a[:] to an owned copy (matching its idiomatic meaning; explicit spans remain available via a[0:len(a)] or a Span constructor), or emit a one-time warning when a slice view of a mutable list is written through / bound to a local that is later mutated. At minimum document the trap prominently next to the list section.

#### D143. compiler.py re-implements codegen's template-emission predicates (documented drift-prone mirrors)

- **Location**: tpyc/compiler.py:66-101, tpyc/compiler.py:103-110
- **Severity / category**: medium / design -- reproduced: yes -- found by `hunt-slop`
- **Problem**: _has_static_protocol_param re-derives 'will codegen emit this function as a C++ template' by re-implementing ProtocolGenerator.get_all_protocol_params shape-walking (RefType/ReadonlyType/OwnType unwrap, Optional/protocol-union/bare-protocol cases) with sema-level predicates, and _is_template_emitted_in_header mirrors FunctionGenerator.is_template_function. Both docstrings admit the mirroring. The completeness-graph reject gate must recognize exactly the same function set as codegen; any change to codegen's unwrap order or a new wrapper type (e.g. a future Send[T]-style qualifier) silently diverges the gate. This is the 'consumer site re-derives the fact' anti-pattern the project's own performance/THIR guidance forbids, in the orchestrator rather than codegen.
- **Evidence**: compiler.py:67 docstring: 'Mirror codegen's static-protocol-param detection (see tpyc.codegen_cpp.protocols.ProtocolGenerator.get_all_protocol_params) using sema-level type predicates only... Used by the completeness-graph reject gate to recognize the same set of in-header-bodied functions as codegen.' Body manually unwraps RefType -> ReadonlyType -> OwnType then special-cases OptionalType, protocol UnionType, bare protocol -- a hand-copied subset of the codegen walk.
- **Fix direction**: Compute 'emitted-in-header' once (sema phase 2 or a shared helper module both consumers import) and store it on FunctionInfo, so the gate and codegen consume the same fact. Sweep note: methods vs free functions vs @cpp_template stubs all flow through _is_template_emitted_in_header; verify each sibling stays in sync if left as-is.

#### D144. Type errors inside macro-generated AST lose source location entirely and don't name the macro

- **Location**: tpyc/macro_api.py:1642-1783 (AstBuilder never sets loc), tpyc/sema/calls.py:5491-5494
- **Severity / category**: medium / design -- reproduced: yes -- found by `macros`
- **Problem**: AstBuilder constructors never set .loc on the nodes they create, and _run_call_macro re-analyzes the returned expansion with no fallback location context. When sema rejects macro-generated code (a macro bug, or a macro that produces code invalid for the user's argument types), the diagnostic has NO line number and no indication of which macro call produced the code -- in a real multi-hundred-line file the user cannot locate the offending call. The macro infrastructure has the call-site loc right there (passed to expand_call_macro) but doesn't stamp it onto loc-less expansion nodes or wrap the re-analysis error. Class macros at least stamp macro_origin on synthesized methods; call-macro expansions get nothing.
- **Evidence**: Repro: @call_macro returning ast.binop(ast.str_lit("a"), "-", ast.int_lit(1)); observed diagnostic: `main_bad.py: error: Invalid operand types for '-': str and IntLiteral(1)` -- file name only, no line/column, no macro name. (Structural junk is handled acceptably: a raw string inside .args produced `main_junk.py:3: error: Unknown expression type: str`, i.e. graceful but anchored at the def line rather than the call.) Contrast: errors the macro itself raises via MacroError/ctx.error are correctly anchored at the call site (expand_call_macro wraps with loc).
- **Fix direction**: After expand_call_macro returns, walk the expansion and stamp the call-site loc onto every node whose loc is None (user arg sub-trees keep their original locs); and/or wrap `self.expr.analyze_expr(expansion)` so SemanticErrors with loc=None get the call-site loc plus 'in expansion of macro <qname>' context. The same treatment applies to builder-trace emit_function bodies (whose fragment locs are deliberately stripped to None) and class-macro-added method bodies.

#### D145. generate_code_to_strings omits cycle_peers: --dump-code / -vv / REPL emit different (uncompilable) code than the file path

- **Location**: tpyc/compiler.py:3699-3713, tpyc/cli.py:594, tpyc/cli.py:610, tpyc/repl.py:439
- **Severity / category**: medium / design -- reproduced: no (code-read evidence) -- found by `orchestration-cli`
- **Problem**: generate_code (file-emitting path) passes cycle_peers=self._cycle_peers.get(mod_name) into codegen so SCC members include <peer>_fwd.hpp and a _fwd header is emitted. generate_code_to_strings never passes cycle_peers, so for any module participating in an import cycle, the string output (used by tpyc --dump-code, -vv, and the REPL) contains mutually-recursive full #include lines that do not match the shipped files and would not compile (pragma-once truncation -> incomplete types). The REPL writes exactly these strings to disk and builds them, so cyclic imports in a preloaded REPL file fail at C++ build even after the include-path bug is fixed.
- **Evidence**: uv run tpy --dump-code on tests/cases/imports/mutual_type_annotation sources:
  a.hpp contains #include "b.hpp"; b.hpp contains #include "a.hpp"
Expected snapshots (file path):
  expected/include/a.hpp:7: #include "b_fwd.hpp"
  expected/include/b.hpp:7: #include "a_fwd.hpp"
compiler.py:3705-3713: codegen.generate(... ) called without cycle_peers=, unlike _generate_code_impl (line 3675-3681).
- **Fix direction**: Pass cycle_peers=self._cycle_peers.get(compiled.name, frozenset()) in generate_code_to_strings, and have string consumers that build (REPL) also emit/write the _fwd.hpp via codegen.generate_fwd_header. The dump-code printer in cli.py:600 should also print the override include path rather than 'include/{name}.hpp'.

#### D146. REPL backends never apply link flags, third-party deps, or runtime .cpp sources

- **Location**: tpyc/repl_backends.py:240-245, tpyc/repl.py:361-367
- **Severity / category**: medium / design -- reproduced: no (code-read evidence) -- found by `orchestration-cli`
- **Problem**: The CLI build path collects `# tpy: link()` flags (compiler.collect_link_flags), resolves managed third-party deps (resolve_build_plan -> PCRE2 sources/flags), and compiles runtime/cpp/src/**.cpp (socket_impl.cpp) into the binary. The REPL's CompileBackend links with only self._config.link_flags, which is the default-constructed empty CppCompilerConfig -- repl.py never calls collect_link_flags/resolve_build_plan/discover_runtime_cpp_sources, and ClangReplBackend has no linking hook at all. Consequence: once the include-path bug (finding 1) is fixed, `import re` (needs PCRE2 objects) or `import socket` (needs socket_impl.cpp) in a REPL will compile at sema/codegen level and then fail at link with undefined symbols. Currently masked by finding 1, which breaks every REPL input earlier.
- **Evidence**: repl_backends.py:240-245 link_cmd = [*self._config.compiler, '-o', ..., *all_objs, *self._config.link_flags]  # link_flags never populated
grep for collect_link_flags/third_party/discover_runtime_cpp_sources in repl.py + repl_backends.py: zero hits.
lib/tpy/_bindings/pcre2.py:3: # tpy: link("pcre2", managed=True); runtime/cpp/src/stdlib/socket_impl.cpp exists.
- **Fix direction**: After compile() in _try_compile_and_run, thread compiler.collect_link_flags() + resolve_build_plan(compiler.collect_third_party_deps(), ...) + discover_runtime_cpp_sources() into the backend (extend REPLBackend.execute or backend config). ClangReplBackend additionally needs %lib-style loading or an explicit unsupported-import diagnostic.

#### D147. Macro module load failures presented as 'Internal error' with no macro-file location

- **Location**: tpyc/macro_loader.py:473-479
- **Severity / category**: low / design -- reproduced: yes -- found by `macros`
- **Problem**: load_module wraps any exception from executing a macro module as a bare RuntimeError, which the CLI then presents with the 'Internal error:' prefix reserved for compiler bugs. A perfectly ordinary user mistake in a macro module (e.g. importing a disallowed module, a typo'd name at module level) is thus presented as a compiler crash, and the line number inside the macro module is reduced to str(e) -- the traceback's file:line into the macro source is dropped from the user-facing message.
- **Evidence**: Observed while testing: a macro module containing `from tpyc.parse import TpyCall` produced:
`Internal error: Error executing macro module 'macro_crash' (/tmp/agents/audit/deps/macro_crash.py): Macro module 'macro_crash' cannot import 'tpyc.parse' -- only tpyc.macro_api and other macro modules are allowed`
-- correct content, wrong framing ('Internal error'), and for e.g. a NameError there would be no line number into the macro file.
- **Fix direction**: Raise a diagnostic-bearing error type (SemanticError/ParseError equivalent) carrying the macro module path and, when available, the innermost traceback frame's line number within that file, so the CLI renders it as a normal compile error.

#### D148. Cross-kind inconsistencies in macro invocation plumbing (tmp-counter reset, kwarg validation, deferred-callback exception wrapping, bool-as-int)

- **Location**: tpyc/sema/function_macros.py:44-63, tpyc/macro_loader.py:221-235, tpyc/macro_loader.py:205-219, tpyc/sema/macros.py:124-137
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `macros`
- **Problem**: The four macro kinds invoke user macro code through four slightly different wrappers with diverging behavior: (1) class macros (validate_and_call_macro), call macros (expand_call_macro), and builder handlers (_wrap_macro_call) all call ast.reset_tmp_counter() before invocation, but run_function_macros does NOT -- function-macro fresh_tmp names depend on which macros ran earlier in the compilation, causing avoidable snapshot churn and naming asymmetry. (2) Class macros get full kwarg validation (unknown-kwarg, missing-required, annotation type-check); function macros get none -- a bad kwarg surfaces as a wrapped raw TypeError. (3) Deferred class-macro callbacks (_apply_deferred_class_macros) catch only MacroError; a generic exception from a deferred callback escapes as an un-wrapped compiler traceback, unlike the eager path which converts any Exception to a SemanticError. (4) validate_and_call_macro's annotation check uses bare isinstance, so a bool value passes an `int`-annotated kwarg (the BuilderContext extractors explicitly exclude bool from int -- inconsistent strictness).
- **Evidence**: function_macros.py:55 `macro_fn(fmctx, **kwargs)` with no reset_tmp_counter and no signature validation, vs macro_loader.py:224 `_ast_builder.reset_tmp_counter()` + lines 159-219 validation in the class-macro path. macros.py:128-131 catches only MacroError; macro_loader.py:229-235 (eager path) also catches `Exception`. macro_loader.py:214 `if not isinstance(value, ann)` vs macro_api.py:949 `if not isinstance(v, int) or isinstance(v, bool)`.
- **Fix direction**: Route all four kinds through one shared invoke helper (reset counter, MacroError->SemanticError, SemanticError pass-through, Exception->SemanticError with macro qname) and share the kwarg-validation step between class and function macros. Gap-sweep note: the deferred-callback path also skips the field-default macro-function pre-processing the eager path does (call_macro_field_function), if a deferred macro inspects fld.default_obj.

#### D149. No determinism guard for compile-time macro execution (hash randomization)

- **Location**: tpyc/macro_loader.py:24-31, 449-505
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `macros`
- **Problem**: Macro modules execute under the host CPython with PYTHONHASHSEED unset, so str hash randomization is live. Any macro that iterates a Python set (or pre-3.7-style assumes dict order from set-derived keys) of field names / type names produces a different iteration order per compiler process, yielding run-to-run nondeterministic generated C++ -- which silently defeats the snapshot tests, ccache, and the content-addressed stdlib object cache (cache-key churn). The loader's restriction layer is explicitly about API-surface forward-compatibility, but nothing in the loader, the API docs, or the macro contexts addresses determinism; the in-repo macros happen to use lists/dicts so the corpus never exposes it.
- **Evidence**: macro_loader.py has no PYTHONHASHSEED handling or re-exec; _make_restricted_builtins removes open/exec/eval but leaves set/frozenset/hash fully available. Note the loader itself is careful elsewhere (dir() is sorted; fresh-name counters are deterministic), so set-iteration in user macro code is the remaining nondeterminism channel.
- **Fix direction**: Either document the determinism contract for macro authors (and add a lint/check in the macro API for set iteration where feasible), or have the tpy/tpyc entry point pin PYTHONHASHSEED=0 via re-exec (also fixes any future reliance on hash order in the compiler itself). Relevant to the future self-hosted-VM plan (a VM can enforce determinism; today nothing does).

#### D150. Parallel C++ build: failure with empty stderr proceeds to link; subsequent failing TUs reported as 'compiled'

- **Location**: tpyc/cli.py:749-758, tpyc/repl_backends.py:214-236
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `orchestration-cli`
- **Problem**: Both parallel compile loops use the captured stderr string as the failure flag: `if r.returncode != 0 and not failed_stderr: failed_stderr = r.stderr`. (a) A failing compile that emits empty stderr (OOM-killed cc1plus, SIGKILL) leaves failed_stderr empty, so `if failed_stderr:` is false and the build proceeds to the link step, surfacing as a confusing missing-.o link error instead of a compile failure. (b) In cli.py, a second failing TU falls into the else branch and is printed as '  compiled <file>'. (c) In repl_backends.py the same empty-stderr fallthrough then runs the hash-cache update loop (lines 233-236) for ALL files including the failed one, so the failed TU is recorded as up to date and is never recompiled -- a previously-built stale .o for that path can be silently linked into subsequent REPL binaries.
- **Evidence**: cli.py:749-754:
  for future in as_completed(futures):
      r, elapsed = future.result()
      if r.returncode != 0 and not failed_stderr:
          failed_stderr = r.stderr
      else:
          progress.compiled(_cpp_name(futures[future]), elapsed)
repl_backends.py:228 `if failed_stderr:` gates the early return; lines 233-236 update _cpp_hashes/_obj_cache unconditionally afterwards.
- **Fix direction**: Track failure with a boolean (any returncode != 0), not stderr truthiness; report each failed TU distinctly; in CompileBackend update hashes only for TUs that actually compiled.

#### D151. ClangReplBackend silently keeps stale definitions on redefinition and misclassifies program stderr containing 'error:'

- **Location**: tpyc/repl_backends.py:415-424, tpyc/repl_backends.py:446-448, tpyc/repl_backends.py:756-776
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `orchestration-cli`
- **Problem**: Two correctness gaps in the opt-in clang-repl backend: (1) declaration diffing uses _diff_lines(insert_only=True), which drops 'replace' opcodes -- redefining a function or class in the REPL produces a replace op, so the new body is never sent to clang-repl and subsequent calls silently run the OLD definition while the REPL reports success. (2) failure detection is `if stderr_output and 'error:' in stderr_output.lower()` -- a user program that legitimately writes 'Error: ...' to stderr (sys.stderr.write) is misclassified as a failed execution, and the statement is not accumulated.
- **Evidence**: repl_backends.py:415-416: new_decls = self._diff_lines(self._prev_hpp_lines, hpp_decls, insert_only=True)
repl_backends.py:769: include = {'insert'} if insert_only else {'insert', 'replace'}
repl_backends.py:446: if stderr_output and 'error:' in stderr_output.lower(): return BackendResult(False, ...)
- **Fix direction**: For (1): detect replace ops on decls and either restart the clang-repl process replaying the new state, or surface 'redefinition not supported by clang-repl backend'. For (2): rely on clang-repl's own diagnostics framing (the stderr sentinel protocol could capture only the segment before the program's own output) or match a stricter pattern (e.g. lines starting with 'repl:'/'input_line').
- **Verifier adjustment**: Part (1) as described is WRONG; part (2) is real. (1) The REPL accumulates source (repl.py:_process_input appends to accumulated_lines), so redefining f produces a workspace containing BOTH defs; the generated hpp/cpp contain both bodies and the decl diff is an INSERT, not a replace. Verified by simulation driving ClangReplBackend._extract_toplevel_decls + _diff_lines on real compiler output for v1='def f: return 1' vs v2=v1+'def f: return 2': insert-only diff = ['::tpy::BigInt f();', '::tpy:...

#### D152. Compiler-level warnings emitted without source locations (LSP tie-breaker violation)

- **Location**: tpyc/compiler.py:3830-3835, tpyc/compiler.py:3899-3904, tpyc/cli.py:558
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `orchestration-cli`
- **Problem**: The 'library module has no cpp_namespace directive' and 'include path collision' warnings are constructed as Diagnostic(WARNING, msg) with no SourceLocation and no file; the CLI then formats them with the program name as filename ('tpyc: warning: ...'). This contradicts the documented LSP tie-breaker that diagnostics carry structured file+span locations, and these warnings genuinely have an attributable file (the module's path / its first line). Most other diagnostics in the audited files do carry locations.
- **Evidence**: compiler.py:3831-3835:
  self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, f"library module '{name}' has no # tpy: cpp_namespace directive..."))  # no loc
cli.py:558: print(diag.format(prog_name), ...) -> formats as 'tpyc: warning: ...'.
- **Fix direction**: Attach SourceLocation(1, 0, file=str(compiled.path)) (or the directive-block location) to both warnings; Diagnostic.format already prefers loc.file over the caller-supplied fallback.

#### D153. Directive typo '# tpy:include(...)' (no space after colon) is silently ignored with no warning

- **Location**: tpyc/parse/parser.py:204, tpyc/parse/parser.py:283-298
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `parser`
- **Problem**: _DIRECTIVE_LINE_RE requires whitespace after 'tpy:' (to avoid matching C++ namespace comments like '# tpy::Foo'). A user writing '# tpy:include("foo.h")' gets the directive dropped entirely -- no include emitted, no 'unknown directive' or 'invalid syntax' warning -- while the equally wrong '# tpy: link_libs("x")' does get a warning. Misspelled-but-recognizably-directive-shaped lines should warn; a missing include surfaces much later as an opaque C++ error.
- **Evidence**: /tmp/agents/directive.py with '# tpy:include("foo.h")' and '# tpy: link_libs("x")': output contains only `directive.py:2: warning: unknown # tpy: directive: 'link_libs'`; line 1 produces nothing and foo.h is absent from the generated code.
- **Fix direction**: Add a second looser regex (e.g. ^#\s*tpy:(?!:)\S) that, when the strict regex fails, emits a 'malformed # tpy: directive (missing space after colon?)' warning. The tpy:: namespace-comment exclusion stays intact via the (?!:) guard.

#### D154. __all__ += / __all__.append() silently ignored by star-export scanning

- **Location**: tpyc/parse/imports.py:41-73, tpyc/parse/imports.py:76-113
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `parser`
- **Problem**: read_module_all only matches ast.Assign / ast.AnnAssign whose target is the Name __all__, taking the LAST literal assignment. ast.AugAssign (`__all__ += ["x"]`) and mutation calls (`__all__.append(...)`) -- both common CPython idioms -- are neither folded in nor flagged via NonLiteralAllError, so star-import expansion and export filtering silently use a stale subset. The docstring promises 'raises NonLiteralAllError when __all__ is defined but not a literal', which these forms violate in spirit. Additionally ast.literal_eval accepts non-string elements (e.g. __all__ = [1]) without validation despite the error message claiming 'list/tuple/set of string literals'.
- **Evidence**: imports.py:57-65 walk: only `isinstance(node, ast.Assign)` and `isinstance(node, ast.AnnAssign)` arms exist; no ast.AugAssign / ast.Expr-call arm. A module with `__all__ = ["a"]; __all__ += ["b"]` exports only 'a' through `from m import *` with no diagnostic.
- **Fix direction**: Detect AugAssign / method-call mutation of __all__ in the same walk and raise NonLiteralAllError (the existing 'not a compile-time literal' diagnostic path), and validate elements are str.

#### D155. Frontend-IR lowering passes empty name_to_origin to record methods -- imported-callable tagging lost inside method bodies

- **Location**: tpyc/frontend_ir/lower.py:806-808, tpyc/frontend_ir/lower.py:789-791
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `parser`
- **Problem**: lower_module builds name_to_origin from FromImports and threads it through free-function bodies and top-level stmts so bare-name calls get resolved_import tagged (mirroring Parser._resolve_call_import). But _lower_record lowers methods with a literal `{}` (`_lower_function(m, {}, ...)`) and field defaults likewise (`_lower_expr(f.default, {}, ...)`), so a plugin record method calling a FromImport-ed function never gets resolved_import. The Python parser tags method bodies identically to function bodies, so this is a fidelity gap between the two front ends; downstream passes that key off resolved_import (builder-trace expander, import-origin checks) will behave differently for plugin-emitted methods than for equivalent .py source.
- **Evidence**: lower.py:807-808: `lowered = _lower_function(m, {}, plugin_name, fm, diags, is_method=True, registry=registry)` vs lower.py:374-375 for free functions: `_lower_function(fn, name_to_origin, ...)`.
- **Fix direction**: Pass the module's name_to_origin through _lower_record into method lowering and field-default lowering. Gap-sweep note: also check _lower_pattern's MatchValue arm (lower.py:971) which passes {} for the same reason.

#### D156. _enrich_literal_types pins int-literal enrichment base to INT32 regardless of configured default_int

- **Location**: tpyc/sema/calls.py:108-109
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `sema-calls`
- **Problem**: When any candidate has a LiteralType param, int-literal args are enriched as `LiteralType(INT32, ...)` with a hardcoded INT32 base, ignoring ctx.default_int_type (Int64/BigInt configurations). Today's matching paths mostly compare literal value-sets and `is_int_base()`, so no concrete misbehavior was found, but any future base-sensitive comparison (e.g. the `actual.base_type == expected` arm in _check_compat:1184) will silently diverge under non-default int width. Cheap hygiene fix consistent with the per-case `default_int` option.
- **Evidence**: calls.py:108-109: `elif isinstance(arg_t, IntLiteralType) and arg_t.value is not None:\n    enriched.append(LiteralType(INT32, (LiteralValue(LiteralTag.INT, arg_t.value),)))`
- **Fix direction**: Use `self.ctx.default_int_type` (threaded in, as the other literal paths do) as the base instead of INT32.

#### D157. _match_record_with_inference matches generic records by short name, not qualified name

- **Location**: tpyc/sema/type_ops.py:1167
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `sema-expr`
- **Problem**: The record branch of generic inference matches `arg_type.name == param_type.name` (short names). Two records named e.g. `Wrapper` from different modules would unify during type-param inference, binding a foreign instantiation's type args to the param. Sibling code paths compare qualified_name() (e.g. _apply_lhs_hint_to_function_return at type_ops.py:1457, seed patterns built with _module_qname). Not reproduced end-to-end (needs a cross-module same-name collision reaching a generic call), but the asymmetry with the qname-based siblings makes it a latent wrong-inference source as multi-module programs grow.
- **Evidence**: type_ops.py:1167: `if not (isinstance(arg_type, NominalType) and arg_type.is_record and arg_type.name == param_type.name): return False` -- contrast :1457 `if ret_pattern.qualified_name() != hint.qualified_name(): return`.
- **Fix direction**: Compare qualified_name() with a fallback to short name only when one side lacks a module qname (parser bare-name case), mirroring validate_type's resolution order.
- **Verifier adjustment**: The asymmetry is real: tpyc/sema/type_ops.py:1167 compares `arg_type.name == param_type.name` (short names) while the sibling _apply_lhs_hint_to_function_return at :1457 uses qualified_name(). I built the cross-module collision the finder did not (/tmp/agents/verif/m6: moda.py and modb.py each define `class Wrapper[T]`; moda defines `get[T](w: Wrapper[T]) -> T`; main calls get(modb.Wrapper(1))). Result: inference DOES unify by short name and binds T=Int32 from the foreign Wrapper, but the dow...

#### D158. Union canonical ordering is not canonical: sort key str(t) ties broken by insertion order

- **Location**: tpyc/typesys.py:3409
- **Severity / category**: low / design -- reproduced: yes -- found by `typesys`
- **Problem**: make_union sorts deduped members by `str(t)` to get a 'canonical' order, but NominalType.__str__ renders only the short name (no qname), so two distinct members with equal display strings (same-short-name records from different modules, same-short-name RecursiveAliasInstanceTypes) tie, and Python's stable sort preserves insertion order. The same semantic union spelled `A | B` in one module and `B | A` in another produces two unequal UnionTypes with different std::variant member orderings -- breaking type equality (set/dict identity, union_alias_names / union_wrapper_index keyed by the members tuple) and producing layout-incompatible variants across module boundaries. str(t) is also compilation-context-dependent for unions via union_display_names, making the 'canonical' key environment-sensitive in principle. Latent today only because the placeholder-collapse bug (separate finding) fires first for the no-qname case.
- **Evidence**: API repro: a = NominalType('Thing', _module_qname='mod_a.Thing'); b = NominalType('Thing', _module_qname='mod_b.Thing'); make_union(a, b) != make_union(b, a); member qname orders are ['mod_a.Thing','mod_b.Thing'] vs ['mod_b.Thing','mod_a.Thing'].
- **Fix direction**: Sort by a fully-discriminating stable key, e.g. (str(t), qualified_name() or '', repr-of-structure), or by a dedicated canonical_sort_key() method that includes _module_qname and type-arg keys recursively.
- **Verifier adjustment**: API behavior confirmed: make_union(NominalType('Thing', qname='mod_a.Thing'), NominalType('Thing', qname='mod_b.Thing')) != make_union(b, a); member qname orders are ['mod_a.Thing','mod_b.Thing'] vs ['mod_b.Thing','mod_a.Thing'] (typesys.py:3409 sorts by str(t), which renders short name only). But severity should be low, not medium: the issue is latent with no constructible end-to-end breakage today -- annotation-path unions with same-short-name members collapse first (the placeholder bug, se...

#### D159. get_element_type() on dict_values/dict_items returns the KEY type

- **Location**: tpyc/typesys.py:4067-4076, tpyc/type_def_registry.py:797-805
- **Severity / category**: low / design -- reproduced: yes -- found by `typesys`
- **Problem**: _ELEMENT_FROM_FIRST_ARG_CATEGORIES includes TypeCategory.DICT_VIEW, and the dict_keys/dict_values/dict_items TypeDefs register no element_of override (unlike builtins.dict, which overrides to args[1]). So NominalType.get_element_type() on dict_values[K,V] returns K, and on dict_items[K,V] returns K instead of tuple[K,V]. Mainstream paths (for-loop, membership, generic Iterable[T] inference) are unaffected because they route through the __iter__ stub via get_iterable_element_type (verified: `for v in d.values()` infers int32_t; `'a' in d.values()` correctly rejected). But get_element_type is called from ~35 sema/codegen sites (overloads.py:222, calls.py:2720, narrowing.py:166, ...), so this is a latent wrong fact waiting for the first consumer that touches a dict view -- exactly the 'consumer re-derives the fact' bug class CLAUDE.md warns about.
- **Evidence**: API repro: make_dict_values_view(STR, INT32).get_element_type() -> str; make_dict_items_view(STR, INT32).get_element_type() -> str (expected Int32 and tuple[str, Int32] respectively). End-to-end iteration/membership tested OK (stub path), so latent only.
- **Fix direction**: Register element_of on the three dict-view TypeDefs (values -> args[1], items -> TupleType((args[0], args[1])), keys -> args[0]) and remove DICT_VIEW from the first-arg fallback set.

#### D160. find_record_by_qname short-name fallback can return an unrelated local record

- **Location**: tpyc/typesys.py:5391-5397
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `typesys`
- **Problem**: find_record_by_qname('othermod.Foo'), when 'othermod' has no ModuleInfo entry and the qname misses both qname indexes, falls back to `self.records.get('Foo')` -- which may be a completely different local class that merely shares the short name. The comment justifies the fallback for the entry-point __main__ case (a module never sees its own ModuleInfo during analyze), but the fallback is not gated on module_name being the current module, so any unresolvable foreign qname silently resolves to a local shadow. Consumers include is_exception_type (typesys.py:236-246), so a dotted exception name from an unregistered module could be classified via an unrelated local class.
- **Evidence**: if "." in qname: module_name, short = qname.rsplit(".", 1); result = self.find_module_record(module_name, short); if result is not None: return result; return self.records.get(short)  # typesys.py:5391-5396
- **Fix direction**: Gate the short-name fallback on module_name matching the analyzing module's own name (pass current_module in, as qualify_exception_name already does), returning None for genuinely-foreign unresolvable qnames.

#### D161. Per-compilation mutable state still module-level beyond the documented asymmetry

- **Location**: tpyc/typesys.py:1198-1199, tpyc/typesys.py:2879, tpyc/type_def_registry.py:253
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `typesys`
- **Problem**: CLAUDE.md mandates per-compilation state on the Compiler instance, naming _dynamic_attached_qnames as the one known exception. But the Send/Sync re-entrancy guards _evaluating_send / _evaluating_sync and the alias-value guard _evaluating_alias_value are also module-level mutable sets, reset only via clear_all_compilation_state(). Two Compiler instances in one process (REPL + background compile, or future parallel Phase-1) would share and cross-pollute these cycle guards (a type mid-evaluation in compiler A returns the greatest-fixed-point True answer to compiler B). Additionally, attach_dynamic_type_def mutates payloads of STATIC TypeDef objects shared across all compilations (acknowledged in the file's own comment as a 'known shared-state weakness'), and clear_dynamic_type_defs resets compiler-owned dicts only when a compiler is active -- a stale active-compilation's dynamic entries survive if clear runs with no current compiler.
- **Evidence**: _evaluating_send: set['NominalType'] = set()  # typesys.py:1198; _evaluating_alias_value: set[tuple] = set()  # typesys.py:2879; _dynamic_attached_qnames: set[str] = set()  # type_def_registry.py:253 plus td.record/protocol/enum mutation of static entries in attach_dynamic_type_def (type_def_registry.py:328-340).
- **Fix direction**: Move the three guard sets onto Compiler (read via compilation_context like native_cpp_names), and fold into the planned 'sema stops writing payloads back into the static registry' cleanup.
- **Verifier adjustment**: The cited module-level sets exist as claimed (_evaluating_send/_evaluating_sync at typesys.py:1198-1199, _evaluating_alias_value at typesys.py:2879, _dynamic_attached_qnames at type_def_registry.py:253 with static-TypeDef payload mutation at 328-340). But two of the three claimed harms do not hold under closer reading: (1) the guard sets are per-call-stack, not per-compilation -- every entry is added then removed in try/finally within a single is_send/is_sync/is_value_type walk (typesys.py:10...

#### D162. Implicit float64 -> Float32 narrowing coercion silently loses precision

- **Location**: tpyc/coercions.py:288-294
- **Severity / category**: low / design -- reproduced: no (code-read evidence) -- found by `typesys`
- **Problem**: The float_to_float32 rule allows implicit narrowing in every context (ASSIGN/INIT/ARG/RETURN) with a bare static_cast<float>, justified as 'matches C++ behavior' -- but TPy's own integer lattice is stricter than C++ (no implicit Int64->Int32; BigInt->fixed gets a runtime check via to_fixed_check). The float lattice is the asymmetric sibling: precision (and range -- double values beyond float range become inf) is silently discarded with no runtime check and no diagnostic. For an HFT audience mixing float64 intermediates into Float32 storage, this is a silent-semantics divergence between the int and float coercion designs rather than a bug per se.
- **Evidence**: Coercion(name='float_to_float32', from_type=is_float64_type, to_type=is_float32_type, codegen=lambda e, _a, _b, _c: f'static_cast<float>({e})')  # comment: 'narrowing, but allowed for convenience -- matches C++ behavior'
- **Fix direction**: Either add a range/precision-checked variant parallel to bigint_to_fixed_int's to_fixed_check, or emit a sema warning at the narrowing site so the asymmetry with the int lattice is deliberate and visible.

#### D163. Borrow-form types report Send of their referent (RefType / pointer-repr Optional)

- **Location**: tpyc/typesys.py:1919-1923, tpyc/typesys.py:2668-2672, tpyc/typesys.py:1751-1761
- **Severity / category**: low / unsound-safety -- reproduced: no (code-read evidence) -- found by `typesys`
- **Problem**: FrameSlot's docstring states the storage-shape rule: 'a borrow-form slot (T&, raw pointer, view of caller storage) is non-Send regardless of T'. But the type-level traits don't encode it: RefType.is_send() returns wrapped.is_send() and OptionalType.is_send() returns inner.is_send() even when uses_pointer_repr() is True (a nullable T* borrow). Frame classification in sema/frame_traits.py re-derives shape-awareness per slot, but any OTHER consumer of is_send -- notably _make_marker, which erases a Send[T] wrapper whenever inner.is_send() holds, and _marker_persists, which peels RefType before deciding persistence -- trusts the value-level answer, so Send[Ref[list[Int32]]] canonicalizes to an erased 'Send' even though it is a live borrow of caller storage. Not reproduced end-to-end (the user-facing surface area of Send markers over borrow-form types was not traced); flagged for the gap-sweep round against sema/frame_traits.py and the Send conversion-site checks.
- **Evidence**: RefType.is_send: `return self.wrapped.is_send()` (typesys.py:1920); OptionalType.is_send: `return self.inner.is_send()` (typesys.py:2669) with no uses_pointer_repr() gate; contrast FrameSlot doc (typesys.py:1815-1819).
- **Fix direction**: Make borrow-form value_form() categories (BORROW_REF, PTR_OPTIONAL over non-value inner) answer is_send() False at the type level, or document explicitly that TpyType.is_send is a value-level trait and audit every non-frame consumer (marker canonicalization first).
- **Verifier adjustment**: Type-level facts verified: RefType.is_send delegates to wrapped.is_send() (typesys.py:1919-1920), OptionalType.is_send delegates to inner.is_send() with no uses_pointer_repr() gate (typesys.py:2668-2669), and the FrameSlot docstring (typesys.py:1812-1820) states the borrow-form-is-non-Send rule. But the unsound-safety framing is overstated: (1) the soundness gate, sema/frame_traits.py, is shape-aware and conservative at every slot kind -- param_slot forces non-Send for any Optional/Union/Type...


## 7. Performance

#### P164. Mutation-propagation cycle fallback is a full-resweep fixpoint without SCC decomposition -- quadratic in module size

- **Location**: tpyc/sema/mutation_propagation.py:66-78, tpyc/sema/mutation_propagation.py:131-192, tpyc/sema/mutation_propagation.py:145
- **Severity / category**: high / perf -- reproduced: yes -- found by `hunt-perf`
- **Problem**: When Kahn's topo sort leaves functions unordered, ALL of them (the actual cycle members PLUS every acyclic caller transitively above any cycle, per the comment at lines 66-70) are dumped into one flat list `cycle_fis` and resolved by fixed-point iteration that re-sweeps the ENTIRE list every round (`for fi in cycle_fis` inside `for _ in range(max_iters)`), recomputing each function's facts from scratch. There is no SCC decomposition, no worklist, and sweep order is declaration order -- so when callers are declared before callees (the common 'main at top, helpers below' style), each sweep advances facts by only one call-graph edge. One small recursive cycle at the bottom of a module pulls the whole caller chain into the fixpoint: worst case O(N) sweeps x O(E) per sweep = O(N*E), quadratic in module size. Invisible at 1k lines; at ~10k lines in one module it already dominates compile time by 85%; a 100k-line module with any recursion reachable from many functions would take minutes-to-hours in this one pass.
- **Evidence**: Generated /tmp/agents/perf/mut_N.py: chain f{N}(x)->f{N-1}(x)->...->f0->base_a<->base_b (base_b does x.append(1)), declared callers-first. Wall times for `uv run tpyc`: N=200 0.66s, N=400 0.88s, N=800 1.47s, N=1600 2.88s, N=3200 9.79s (baseline ~0.45s -> increments 0.21/0.43/1.02/2.43/9.34 = x4 per doubling). cProfile at N=3200 (9.6k-line file): `mutation_propagation.py:131(_resolve_cycle)  1 call  tottime 15.132s  cumtime 17.588s` out of 20.65s total compile; 20.5M dict.items() calls and 10.4M set.add() calls attributed to the sweep.
- **Fix direction**: Tarjan/Kosaraju SCC decomposition of the local call graph; process SCCs in reverse topological order, running the fixpoint only within each SCC (usually 2-3 functions) with a worklist seeded by changed callees. This also stops acyclic callers from entering the fixpoint at all -- they resolve in one `_resolve_single` pass once their SCC predecessors are done. Note this is one of the three areas CLAUDE.md explicitly flags for algorithmic-cliff review (mutation propagation), so it is in-policy to fix despite the no-proactive-frontend-optimization rule.

#### P165. Flow-facts save/merge copies every tracked variable's facts at every branch -- quadratic in function/module-body length (value_ranges merge dominates)

- **Location**: tpyc/sema/init_tracker.py:27-43, tpyc/sema/flow_facts.py:79-105, tpyc/sema/statements.py:1005-1035, tpyc/sema/statements.py:608-640
- **Severity / category**: high / perf -- reproduced: yes -- found by `hunt-perf`
- **Problem**: `InitTracker.save()` materializes 13 frozenset/dict copies of ALL per-function flow state (definitely_assigned, narrowed_types, value_ranges, borrows, ...) and is called 3x per if-statement (before/then/else); `FlowFacts.merge` then rebuilds dicts over all entries, and `_merge_value_ranges` calls `ValueRange.merge` for EVERY tracked integer variable at every join -- not just variables touched in the branch. Additionally `_sync_promoted_var_types`/`_restore_ns_var_types` (statements.py:608-640) walk all namespace vars per branch. Cost per branch is O(total tracked vars), so a body with V vars and B branches costs O(V*B) -- quadratic in body length. This hits both large functions AND module top-level scripts (the top-level body is analyzed as one body with globals tracked), so it is size-proportional for script-style programs and generated/state-machine code.
- **Evidence**: Generated function with N int locals then N if-statements: N=200 0.73s, N=400 1.11s, N=800 2.92s, N=1600 11.29s (increments 0.23/0.61/2.42/10.8 = x4 per doubling). cProfile at N=1600 (~4.8k-line body): merge_branches 7.08s cum (1601 calls), _merge_value_ranges 6.65s with 2,560,000 ValueRange.merge calls (= 1600 ifs x 1600 vars), init_tracker.save 4.24s (4803 calls), _sync_promoted_var_types 1.34s + _restore_ns_var_types 0.98s -- ~17.5s of an 18.6s analysis. Same shape at module top level: N=400 1.12s vs N=800 3.01s (x4 per doubling).
- **Fix direction**: Don't iterate all vars at joins: track a per-branch dirty set (vars whose facts changed since `save()`), and merge only dirty keys against the snapshot; or move FlowFacts to persistent/shared immutable maps (HAMT or parent-pointer deltas) so save() is O(1) and merge is O(changed). The docstring in apply_loop_exit_facts already defers 'proper fixpoint' to THIR/MIR -- this delta representation is the same data structure THIR will want, so it is migration-aligned rather than throwaway. Sibling constructs to check when fixing: while/for loop entry/exit (apply_loop_entry_facts copies the same 13 collections), try/except (statements.py:1099-1177), and match arms (sema/match.py:153,256,552) all share the per-branch full-copy pattern.

#### P166. Quadratic ModuleInfo reconstruction across the workspace in _finalize_declarations

- **Location**: tpyc/compiler.py:2727-2748, tpyc/compiler.py:3481-3552
- **Severity / category**: high / perf -- reproduced: no (code-read evidence) -- found by `orchestration-cli`
- **Problem**: Every module's _finalize_declarations walks its FULL transitive dependency closure and calls _exports_to_module_info for each dep -- which rebuilds the entire variables dict (with a lookup_qualified + qualified_cpp_name per variable), copies reached/recursive_union_names sets, and re-runs _index_builtin_type_records over all records. Sum over modules of |transitive deps| is O(N^2) for chain/DAG-shaped workspaces, with per-dep cost proportional to the dep's export surface. The same conversion is repeated again per module in the implicit-stdlib loop (2756-2776) and once more in _analyze_bodies' refresh (2947-2949). This is exactly the 'invisible on the test corpus, brutal at scale' shape CLAUDE.md warns about, on a size-proportional path (workspace module count x export surface).
- **Evidence**: compiler.py:2729-2748:
  queue = list(compiled.ast.user_module_imports)
  while queue: ... module_info = self._exports_to_module_info(dep_name, dep_compiled.exports, dep_compiled); analyzer.registry.register_module(module_info); ... for transitive in self.modules[dep_name].ast.user_module_imports: queue.append(transitive)
_exports_to_module_info (3481-3552) allocates fresh ModuleVarInfo per variable per call; no caching keyed on (module, phase).
- **Fix direction**: Cache the ModuleInfo per CompiledModule and invalidate at the two well-defined transition points (post-_finalize_declarations, post-_analyze_bodies refresh); decl-phase exports are final after the dep's _finalize_declarations, so all later consumers can share one object. Also consider registering only direct deps + lazy transitive resolution through the shared _shared_modules dict, which already exists for exactly this cross-module surface.

#### P167. Redundant BigInt deep-copy of literal-initialized int locals at every arithmetic use (per-iteration copy in accumulator loops)

- **Location**: tpyc/coercions.py:208-221, tpyc/sema/operators.py:233-317, runtime/cpp/include/tpy/bigint.hpp:222
- **Severity / category**: medium / perf -- reproduced: yes -- found by `hunt-perf`
- **Problem**: An `int` (BigInt) local initialized from a literal (`t = 0`) is tracked by sema with a literal/fixed-narrowed type at its use sites; when it appears as the left operand of arithmetic, codegen applies a to-BigInt coercion wrapper `::tpy::BigInt({expr})` even though the variable's C++ storage is ALREADY `::tpy::BigInt` -- emitting a copy-construction of the accumulator on every evaluation. In the canonical accumulator loop `t = 0; for i in range(n): t = t + i` the copy is baked into the loop body and runs every iteration. With BigInt's small-int tagging the copy is a branch+word for small values, but once the value actually grows beyond int63 it is a heap allocation + limb copy per iteration, roughly doubling bignum accumulation cost -- and `int` is TPy's DEFAULT integer type, so this is the mainstream Python idiom. Once the variable is reassigned from a non-literal source the wrapper disappears (`t = n; u = t + n` emits clean `(t) + (n)`), confirming the literal-tracking origin. Related minor issue: `t += n` lowers to `t = (t) + (n)` and runtime `BigInt::operator+=` itself is `*this = *this + rhs` (bigint.hpp:222), so no in-place big-value addition path exists at all.
- **Evidence**: Repro /tmp/agents/perf/probe7.py: `def f(n: int) -> int: t = 0; u = t + n; t = t + n; t += n; return t + u` emits `::tpy::BigInt u = ((::tpy::BigInt(t)) + (n)); t = ((::tpy::BigInt(t)) + (n)); t = (t) + (n);` -- the first two wrap `t` (already a BigInt) in a copy ctor; the post-reassignment use is clean. Pattern ships in committed snapshots: tests/cases/async/await_in_for_list/expected/src/main.cpp:45 `total = ((::tpy::BigInt(total)) + (__await_lift_0));` (same in await_in_for_range, await_in_while, await_in_for_with_break/else). Control: probe8 with `t = n` emits no wrapper.
- **Fix direction**: The coercion decision should consult the variable's STORAGE type (already BigInt), not the value-range/literal-narrowed sema type: when source storage form is BigInt and target is BigInt, the wrapper must be identity regardless of narrowing. This is an instance of the storage-vs-narrowed-type duality; check siblings: the same wrap appears on comparisons (`(::tpy::BigInt(i) < n)` in async/await_in_while snapshot line 52 -- legitimate there only if i's storage is fixed-int) and possibly on subscripts/function args of literal-valued int locals. Separately consider a true in-place `operator+=` in bigint.hpp for the big-value path.

#### P168. Protocol conformance is recomputed from scratch on every overload-resolution probe (no (type, protocol) memo)

- **Location**: tpyc/sema/protocols.py:164-473, tpyc/sema/protocols.py:825-891
- **Severity / category**: low / perf -- reproduced: no (code-read evidence) -- found by `sema-methods-protocols`
- **Problem**: ProtocolChecker holds no cache: type_conforms_to_protocol / classify_protocol_conformance re-run the full walk -- collect_protocol_methods (recursive parent re-walk with per-parent signature substitution), per-method lookup_record_method_overloads (recursive MRO walk), substitute_types per param -- for every (candidate, arg) pair that resolve_overload probes (overloads.py passes protocol_checker and protocol_classifier callbacks), at every call site, plus satisfies_bound for every generic call. Total work scales as call_sites x candidate_overloads x protocol_methods x inheritance_depth with zero reuse for identical (type, protocol) queries; CLAUDE.md explicitly names protocol conformance checking as an algorithmic-cliff area. Not measured on a large corpus (front-end perf is explicitly not to be proactively optimized), so filed as a design note rather than a regression.
- **Evidence**: grep over protocols.py/overloads.py shows no cache/memo structure; classify_protocol_conformance (protocols.py:223) is pure recomputation; collect_protocol_methods (825) rebuilds the inherited method list per call; type_has_method_with_signature (650) re-walks record MRO per protocol method.
- **Fix direction**: A per-Compiler memo keyed by (type-identity, protocol-identity) for classify_protocol_conformance results (invalidation-free within one compilation since registry payloads are frozen after registration), or at minimum memoize collect_protocol_methods/collect_protocol_fields per protocol name. Note CLAUDE.md's 'per-compilation state belongs on a Compiler instance' rule for the cache home.


## 8. Slop / cruft

#### S169. directly_implements_dynamic duplicated verbatim in sema/protocols.py and codegen_cpp/protocols.py

- **Location**: tpyc/sema/protocols.py:173-197, tpyc/codegen_cpp/protocols.py:298-343
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-slop`
- **Problem**: The same nontrivial predicate (does a concrete record C++-inherit a @dynamic protocol base, with the @native exclusion and the root-level is_dynamic filter) is implemented twice with byte-identical bodies; the sema copy even says 'Mirrors codegen's same-named helper'. This is the exact sema/codegen mirroring-drift pattern the project's own guidelines flag: a change to the inheritance rule in one copy silently desynchronizes Adapter-substitution decisions made at sema time (TpyCall.representational_subst_params) from the lowering decision made at codegen time, producing wrong adapter wrapping rather than a loud failure. The codegen copy also carries a 5-paragraph narrative docstring.
- **Evidence**: sema/protocols.py:173 'def directly_implements_dynamic(self, concrete, protocol): """...Mirrors codegen's same-named helper..."""' and codegen_cpp/protocols.py:298 have identical bodies: 'if not isinstance(...) or not ... .is_user_record: return False / record_info = ...get_record(...) / if record_info is None or record_info.is_native: return False / for p in record_info.implemented_protocols: pi = protocol_info_of(p); if pi is None or not pi.is_dynamic: continue; if p.name == proto_name or is_subtype(pi, proto_name): return True'. Only difference is self.ctx.registry vs self.ctx.analyzer.registry.
- **Fix direction**: Hoist to a single free function (e.g. in sema/protocols.py or a shared module) taking the registry as a parameter, or better, have sema decide once and materialize the fact on the call AST node (per the CLAUDE.md THIR guidance). Gap for sweep: check whether other protocol-conformance predicates (is_subtype walks, protocol_info_of chains) have similar sema/codegen twins.
- **Verifier adjustment**: Duplication is real and verified: sema/protocols.py:185-197 and codegen_cpp/protocols.py:330-342 are byte-identical modulo self.ctx.registry vs self.ctx.analyzer.registry, and BOTH copies are live (sema copy at sema/protocols.py:56 and sema/type_ops.py:1530; codegen copy at codegen_cpp/expressions.py:301/798/917/2364, statements.py:969/997, protocols.py:277). The codegen docstring (lines 299-329) is genuinely ~5 paragraphs. However, severity recalibrates to low: the copies are currently consi...

#### S170. Guarded-union match end label emitted at column 0

- **Location**: tpyc/codegen_cpp/match.py:1186
- **Severity / category**: low / slop -- reproduced: no (code-read evidence) -- found by `codegen-stmt-match`
- **Problem**: _gen_match_guarded_union writes `out.write(f"{end_label}:;\n")` without the indent prefix, unlike the sibling _gen_match_guarded_record (match.py:1752) and _gen_match_switch_str (1592) which write `f"{indent}{end_label}:;\n"`. Cosmetic-only (generated C++ misindented), but it is exactly the kind of sibling drift the emit helpers are supposed to prevent.
- **Evidence**: match.py:1186: `out.write(f"{end_label}:;\n")` vs match.py:1752: `out.write(f"{indent}{end_label}:;\n")`.
- **Fix direction**: Add the indent prefix.

#### S171. Dead `std::move` on const pointer deref in the Own[Optional] arg conversion

- **Location**: tpyc/codegen_cpp/expressions.py:423-424
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-borrow-storage`
- **Problem**: The Own[Optional[T]] sink conversion emits `std::optional<T>(std::move(*result))` even when `result` is a `const T*`: std::move on a const lvalue silently selects the copy constructor, so the move is a no-op that misleads readers (and clang-tidy performance-move-const-arg would flag it). It also obscures the real semantics decision that the same line gets catastrophically wrong for non-const pointers (see the move-out finding) -- the emit site cannot express 'copy here, move there' because it never consults ownership.
- **Evidence**: Repro /tmp/agents/bf/y2_siblings.py append_opt: `void append_opt(std::vector<std::optional<A>>& xs, const A* b) { xs.push_back(b ? std::optional<A>(std::move(*b)) : std::nullopt); }` -- moves from `const A&`, i.e. copies.
- **Fix direction**: Emit a plain copy for const/borrowed sources; reserve std::move for genuinely consumable Own sources (same fix as the critical move-out finding).

#### S172. Doc rot around narrowing/readonly soundness claims (three contradictions in safety-critical docs)

- **Location**: docs/READONLY_DESIGN.md:231-234, docs/NONE_SAFETY.md:130-147 and 178-183, docs/READONLY_DESIGN.md:256-257
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-readonly-narrowing`
- **Problem**: (1) READONLY_DESIGN says 'Expression-identity narrowing for fields/subscripts was removed ... Narrowing only applies to local variable names' and NONE_SAFETY's Known Limitations repeats it with an example claiming 'if obj.field is not None: obj.field.method()' won't narrow -- but field-path narrowing IS implemented and live (the n4/n5 repros show 'if h.p is not None' narrows h.p, unsoundly). The docs describe the removed design while the shipping code carries the unsound variant. (2) NONE_SAFETY says readonly bodies 'reject writes to globals'; READONLY_DESIGN says global writes are allowed -- code allows them, including FIELD writes through a global alias inside an @readonly method (r17 compiled clean), which is a readonly-contract escape worth documenting deliberately. (3) NONE_SAFETY status table marks loop invalidation 'Done/Covered' while the loop back-edge hole (critical finding above) is open. Misleading docs in exactly the area where reviewers and the gap-sweep will rely on them.
- **Evidence**: READONLY_DESIGN.md:231-234: 'Narrowing only applies to local variable names.' vs narrowing.py field-path keys (_expr_to_narrowing_key returns dotted paths; n4/n5 generated code elides has_value checks on h.p). NONE_SAFETY.md:179-180: 'rejects writes to globals in readonly bodies' vs r17_readonly_global_write.py: @readonly method body 'gh.p = None' compiles with no diagnostic.
- **Fix direction**: Update both docs to the implemented semantics (field-path narrowing exists with name-rooted invalidation; @readonly permits global writes including field writes through globals), and downgrade the loop-stress 'Done' rows until the back-edge kill is implemented.

#### S173. Dead direct-compile flag-resolution block in build/third_party.py (~55 lines incl. ResolvedLib, resolve_system, resolve_bundled_flags, known_lib_names)

- **Location**: tpyc/build/third_party.py:295-347, tpyc/build/third_party.py:142
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-slop`
- **Problem**: The entire 'Direct-compile flag resolution' section is unreachable: dataclass ResolvedLib is constructed only by resolve_system/resolve_bundled_flags, and neither function is referenced anywhere in the repo (only their def lines match). known_lib_names() (line 142) is likewise never called. resolve_system additionally carries a TODO narrative about pkg-config probing for a path nothing executes. The live build-plan path is resolve_build_plan below it.
- **Evidence**: grep -rn 'ResolvedLib' over tpyc/ returns only third_party.py lines 300/314/325/336/343 (def + self-construction); grep for resolve_system/resolve_bundled_flags/known_lib_names over the whole repo (tpyc, lib, tests, frontends, scripts) returns only the def lines. Code: 'def resolve_system(lib: ThirdPartyLib) -> ResolvedLib: """System-mode direct-compile flags... TODO: pkg-config / brew prefix probing..."""'
- **Fix direction**: Delete ResolvedLib, resolve_system, resolve_bundled_flags, known_lib_names, and the section banner; if system-mode direct-compile is still planned, the design note belongs in docs or a TODO.md entry, not dead code.

#### S174. Cluster of verified-dead private helpers across repl/parse/sema/codegen (~95 lines)

- **Location**: tpyc/repl.py:262, tpyc/parse/imports.py:330, tpyc/sema/protocols.py:104, tpyc/codegen_cpp/types.py:310, tpyc/sema/statements.py:438, tpyc/codegen_cpp/gen_async.py:2901, tpyc/macro_loader.py:446, tpyc/namespace.py:107, tpyc/typesys.py:1401, tpyc/frontend_plugin.py:123
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-slop`
- **Problem**: Ten functions/methods have zero references anywhere in the repo (tpyc/, lib/, tests/, frontends/, scripts/, examples/, pyproject) outside their own def line, verified by token search over all .py/.toml/.md/.cpp/.hpp: ReplSession._get_indent_for_continuation (18 lines), ImportProcessor._resolve_relative_to_absolute (17), record_extends_any free function in sema/protocols (19, 'Standalone utility for callers without ProtocolChecker access' -- no such caller exists), TypeResolver.is_fixed_int_arithmetic (14), StatementAnalyzer._is_in_constructor (6), AsyncCodegen._resume_index_for_case (4; sibling _yield_at_resume IS used), MacroRegistry.is_loaded (2), Namespace.bind_builtin (3), PtrType.as_mutable (5; as_const is live), FrontendRegistry.claims_extension (2). None are visitor-pattern, getattr-dispatched, or entry-point names.
- **Evidence**: Custom AST scanner over tpyc/ (defs whose name token count across all repo text equals def-site count) followed by per-name 'grep -rn <name>' confirmation, e.g.: grep -rn 'record_extends_any' --include='*.py' -> only sema/protocols.py:104; grep -rn '_resume_index_for_case' -> only gen_async.py:2901; grep -rn 'is_fixed_int_arithmetic' -> only codegen_cpp/types.py:310.
- **Fix direction**: Delete all ten. If record_extends_any or _is_in_constructor were meant as API for a pending feature, that intent should live in TODO.md, not unreferenced code.

#### S175. Dead duplicate: registration.get_module_function_overloads shadows the live calls._get_module_function_overloads

- **Location**: tpyc/sema/registration.py:377-382, tpyc/sema/calls.py:1483
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-slop`
- **Problem**: RegistrationManager.get_module_function_overloads (registration.py:377) is never called anywhere; the only live implementation of the same lookup is CallAnalyzer._get_module_function_overloads (calls.py:1483, used at calls.py:991). One copy is dead AND it is a same-name duplicate across sibling sema modules -- a reader extending module-function lookup (e.g. for overload registration) can patch the dead copy and see no effect.
- **Evidence**: grep -rn 'get_module_function_overloads' over the repo returns: registration.py:377 (def, no callers) and calls.py:991/:1483 (call + def of the underscore-prefixed twin). Bodies are equivalent registry lookups: 'module_info = self.ctx.registry.get_module(module_name); if module_info and func_name in module_info.functions: return module_info.functions[func_name]'.
- **Fix direction**: Delete the registration.py copy (or move the single helper onto the registry itself, where both phases naturally find it).

#### S176. Dead backward-compat alias properties is_export / extern_name duplicated on both TpyFunction and FunctionInfo

- **Location**: tpyc/parse/nodes.py:1346-1352, tpyc/typesys.py:4757-4772
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-slop`
- **Problem**: Both the parse-level TpyFunction and the sema-level FunctionInfo carry an is_export property that is a byte-identical duplicate of the adjacent live is_extern_c property (both return linkage == FunctionLinkage.EXPORT_C), and an extern_name property explicitly documented as 'Backward compat alias for native_name'. Neither alias is referenced anywhere in the repo; is_extern_c (4 call sites) and native_name (~140 references) are the live names. Dead compat aliases in two classes invite divergent semantics if anyone ever 'fixes' one.
- **Evidence**: typesys.py:4769-4772: '@property def extern_name(self) -> Optional[str]: """Backward compat alias for native_name.""" return self.native_name'; parse/nodes.py:1346-1348 '@property def is_export(self) -> bool: return self.linkage == FunctionLinkage.EXPORT_C' immediately after the identical is_extern_c. grep -rn '\.is_export\b|\.extern_name\b' over tpyc/ and lib/ returns nothing.
- **Fix direction**: Delete all four alias properties.

#### S177. ~160 unused imports across non-test tpyc modules

- **Location**: tpyc/codegen_cpp/expressions.py:17-65, tpyc/codegen_cpp/statements.py:13-30, tpyc/sema/analyzer.py, tpyc/sema/expressions.py, tpyc/compiler.py:27-57, tpyc/frontend_ir/lower.py:18-122 (full list via ruff F401)
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-slop`
- **Problem**: ruff --select F401 (excluding test_* and __init__.py re-export shims) reports ~160 unused imports concentrated in the largest modules (sema/expressions.py 15, sema/analyzer.py 14, codegen_cpp/statements.py 10, sema/statements.py 10, generator.py 8, frontend_ir/lower.py 7...). Several are mid-function imports (codegen_cpp/statements.py:5371 is_list, generator.py:1108 TpyProtocol, protocols.py:844 ProtocolInfo) indicating refactors that removed the use but not the import. At this scale it actively misleads dependency reading (e.g. codegen_cpp/expressions.py appears to depend on polymorphic_source_is_pointer and PendingListType but does not).
- **Evidence**: uvx ruff check --select F401 tpyc/ --exclude 'tpyc/test_*' -> 'Found 184 errors, 160 fixable'; sample: 'tpyc/codegen_cpp/expressions.py:20 `..typesys.INT32`, `..typesys.BIGINT`, `..typesys.FLOAT`, `..typesys.polymorphic_source_is_pointer` imported but unused'; 'tpyc/codegen_cpp/statements.py:5371:68 `..type_def_registry.is_list` imported but unused' (function-local import).
- **Fix direction**: Run ruff --fix for F401 once (mechanical, no snapshot impact), and consider adding F401 to a lint gate so it does not re-accumulate. Note tpyc/parse/__init__.py's 10 hits are intentional re-exports and should get __all__ instead of deletion.

#### S178. Historical/transient narrative comments violating the repo's own comment policy

- **Location**: tpyc/codegen_cpp/gen_async.py:3349, tpyc/codegen_cpp/functions.py:100-102, tpyc/parse/type_resolver.py:204-205, tpyc/sema/mutation_propagation.py:2, tpyc/compiler.py:2271, tpyc/compiler.py:2582
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-slop`
- **Problem**: Several comments carry exactly the dead-history narrative CLAUDE.md forbids: gen_async.py:3349 references 'the legacy `_gen_generator_for_*` peephole init' -- no such symbol exists anywhere in the repo anymore (verified by grep), so the cross-reference is unresolvable; functions.py:102 'misnamed `_FIXED_INT_NAMES` historically' explains a rename that already happened; type_resolver.py:205 'flag name is historical and worth renaming to is_value_position' is a TODO-in-comment; mutation_propagation.py:2 'Phase 2 of parameter mutation inference (8a)' references a plan-item number ('8a') that exists in no doc; compiler.py:2271/2582 reference 'Phase 5/Phase 2 of the per-module attribute table refactor' -- refactoring-phase markers for a finished migration.
- **Evidence**: grep -rn '_gen_generator_for' tpyc/ -> only the comment at gen_async.py:3349. functions.py:100-102: '# Zero-arg scalar constructors... -- misnamed `_FIXED_INT_NAMES` historically.' mutation_propagation.py:2: 'Phase 2 of parameter mutation inference (8a).' compiler.py:2271: '# dropped in Phase 5 of the per-module attribute table refactor.'
- **Fix direction**: Delete the stale symbol reference and phase markers; do the type_resolver rename or drop the note. (Mechanical; the surrounding code is correct.)
- **Verifier adjustment**: Five of six locations hold: gen_async.py:3349 references '_gen_generator_for_*' which exists nowhere (grep: only the comment; the live peephole symbols are _gen_simple_for_generator / _gen_simple_while_generator in gen_generators.py); functions.py:100-102 'misnamed _FIXED_INT_NAMES historically' rename note confirmed; type_resolver.py:204-206 'worth renaming to is_value_position' TODO-in-comment confirmed; compiler.py:2271 and 2582 'Phase 5/Phase 2 of the per-module attribute table refactor'...

#### S179. 13 unused local variables, including a stale timing/count scaffold in cli.py and dead indent locals in gen_generators.py

- **Location**: tpyc/cli.py:639, tpyc/codegen_cpp/gen_generators.py:180-182, tpyc/codegen_cpp/gen_generators.py:260-262, tpyc/codegen_cpp/statements.py:1588, tpyc/codegen_cpp/statements.py:3752, tpyc/repl.py:467, tpyc/sema/methods.py:1807, tpyc/macro_loader.py:282, tpyc/sema/builder_trace.py:451
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-slop`
- **Problem**: ruff F841 finds 13 assigned-never-used locals in non-test code. Notable: cli.py:639 'n_cpp = len(all_cpp_paths)' (leftover from a removed progress message), repl.py:467 't_codegen' (timing computed, never reported), gen_generators.py computes ind2/ind3/ind4 indent strings twice in two emitters and uses none of them, sema/methods.py:1807 'sub = MethodAnalyzer._substitute_inline_body' aliases the method then never uses the alias (sub_args is used). builder_trace.py:451 'result = expand_builder_method(...)' is semi-intentional (handler communicates via self._pending_replacement side channel) but unexplained.
- **Evidence**: uvx ruff check --select F841 tpyc/ --exclude 'tpyc/test_*' -> 13 hits, e.g. 'tpyc/cli.py:639:9 F841 Local variable `n_cpp` is assigned to but never used', 'tpyc/codegen_cpp/gen_generators.py:180-182 ind2/ind3/ind4'. Manually confirmed cli.py:639 and methods.py:1807 by reading surrounding code.
- **Fix direction**: Delete the dead locals; for builder_trace.py:451 either drop the binding or add the one-line why (return value intentionally unused, side-channel protocol).

#### S180. involves_variables has two unreachable-distinct branches collapsing into the default

- **Location**: tpyc/codegen_cpp/types.py:303-308
- **Severity / category**: low / slop -- reproduced: yes -- found by `hunt-slop`
- **Problem**: In TypeResolver.involves_variables, the final 'if isinstance(expr, TpyCall): return True' / 'if isinstance(expr, TpyMethodCall): return True' branches are behaviorally identical to the unconditional 'return True' default that immediately follows -- the isinstance tests do nothing. Trivial by itself, but it pattern-matches the 'enumerate cases then default to the same value' filler style and makes the reader hunt for a distinction that does not exist. Its only caller is codegen_cpp/expressions.py:1580 (BigInt literal-folding guard), so simplification is safe.
- **Evidence**: types.py:303-308: 'if isinstance(expr, TpyCall): return True  # Function calls may return BigInt\n if isinstance(expr, TpyMethodCall): return True\n # Default to True for safety\n return True'. grep -rn involves_variables -> single external caller at codegen_cpp/expressions.py:1580.
- **Fix direction**: Keep the TpyCall comment if the why matters, drop the two redundant isinstance branches.

#### S181. @builder_terminal documented return contract (TypeInfo types the LHS) is dead -- value discarded

- **Location**: tpyc/sema/builder_trace.py:449-453, tpyc/macro_api.py:187-199, 352-361
- **Severity / category**: low / slop -- reproduced: no (code-read evidence) -- found by `macros`
- **Problem**: builder_terminal's docstring (and expand_builder_method's) says the terminal handler 'Returns the TypeInfo for the call's result type (so the LHS in x = builder.terminal(...) is statically typed)'. In the expander, `result = expand_builder_method(...)` is assigned and never used; LHS typing actually comes from the emitted replacement function's declared return type. Macro authors reading the API docs will carefully construct and return a TypeInfo that does nothing; conversely a terminal returning None today silently works, so tightening this later is a breaking change.
- **Evidence**: builder_trace.py:451: `result = expand_builder_method(bound, qname, ctx_obj, args, mcall.loc)` -- `result` has no subsequent reads (replacement comes solely from self._pending_replacement). macro_api.py:195-197 docstring promises the return value is used.
- **Fix direction**: Either consume the returned TypeInfo (e.g. cross-check it against the emitted function's return type and error on mismatch) or fix the docstrings in builder_terminal and expand_builder_method to say the return value is ignored.

#### S182. Slop: unimported Optional in annotation; docstring references non-existent ClassInfo.add_field

- **Location**: tpyc/sema/local_deduction.py:383, tpyc/sema/macros.py:72
- **Severity / category**: low / slop -- reproduced: no (code-read evidence) -- found by `macros`
- **Problem**: local_deduction.py:383 annotates `_widen_inferred_type(...) -> Optional[TpyType]` but `Optional` is never imported (only TYPE_CHECKING from typing). Harmless at runtime due to `from __future__ import annotations`, but it's a latent NameError for any future typing.get_type_hints / runtime-annotation use and trips static checkers; the file otherwise uses `X | None` style. macros.py:72's docstring lists `ClassInfo.add_field` among the mutation APIs, but ClassInfo exposes no add_field -- comment rot that misleads macro authors scanning for the field-addition API.
- **Evidence**: local_deduction.py:383: `def _widen_inferred_type(current: TpyType, new_type: TpyType) -> Optional[TpyType]:` with imports at lines 10-58 containing no `Optional`. macros.py:71-72: 'mutate the record via `ClassInfo.add_method` / `add_field` / `set_match_args`' -- grep of macro_api.py shows no add_field definition.
- **Fix direction**: Change the annotation to `TpyType | None`; drop `add_field` from the docstring (or implement it if field synthesis is planned -- the ModuleEmitter future-work note in BuilderContext suggests record-shape mutation is on the roadmap).

#### S183. _compile_impl duplicates the entire 10-step pipeline for stdin vs file inputs

- **Location**: tpyc/compiler.py:1185-1247 vs tpyc/compiler.py:1249-1321
- **Severity / category**: low / slop -- reproduced: no (code-read evidence) -- found by `orchestration-cli`
- **Problem**: The from_source branch and the file branch of _compile_impl each spell out the same sequence (discover implicit stdlib -> discover imports -> compute order -> propagate directives -> native prefix -> namespace/include maps -> pre-populate -> reexport pre-pop -> star-expand/resolve/finalize per module -> __all__ check -> completeness gate -> bodies -> finalize_borrow_checks) as two ~60-line near-clones. They have already drifted slightly (the stdin branch wraps order computation in `if self.resolver`); any new phase must be added twice, and a missed twin silently diverges REPL/-c behavior from file compilation. Direct THIR-migration friction: the lowering pipeline will need a single authoritative phase list.
- **Evidence**: compiler.py:1221-1247 and 1283-1318 are line-for-line parallel loops over self.compile_order calling the same seven methods in the same order, differing only in setup of the entry module.
- **Fix direction**: Factor the post-discovery pipeline (everything from _apply_native_namespace_prefix through finalize_borrow_checks) into one private method; the two branches keep only their distinct discovery preambles.

#### S184. Dead FORBIDDEN_CONSTRUCTS name check and stale docstring claims in parser

- **Location**: tpyc/parse/parser.py:442-444, tpyc/parse/parser.py:3527-3529, tpyc/parse/parser.py:2422-2428
- **Severity / category**: low / slop -- reproduced: no (code-read evidence) -- found by `parser`
- **Problem**: Parser.FORBIDDEN_CONSTRUCTS = {'with', 'async', 'await'} is checked in _parse_expr's ast.Name branch, but all three are hard keywords in Python 3.7+ and can never appear as ast.Name -- ast.parse raises SyntaxError first. The check is unreachable dead code that misleadingly suggests these constructs are rejected (they are in fact fully supported: _parse_with, AsyncFunctionDef, TpyAwait). Similarly the _parse_function docstring (2422-2428) still says 'v1 sema rejects async + ...' / 'PR 3 (codegen)' -- transient narrative the project's comment policy explicitly bans.
- **Evidence**: parser.py:442-444 `FORBIDDEN_CONSTRUCTS = { "with", "async", "await" }`; parser.py:3527-3529 `if node.id in self.FORBIDDEN_CONSTRUCTS: raise ParseError(...)` -- unreachable since these cannot lex as identifiers.
- **Fix direction**: Delete FORBIDDEN_CONSTRUCTS and its check; trim the PR-numbered/migration-phase narration from the async docstrings per the repo's 'no dead history in comments' rule.

#### S185. is_dangling_return comment contradicts behavior for expression callees

- **Location**: tpyc/sema/compatibility.py:2146-2148
- **Severity / category**: low / slop -- reproduced: no (code-read evidence) -- found by `sema-calls`
- **Problem**: Comment reads '# Expression callees return temporaries (not dangling)' followed by `return True` -- in this predicate True means dangling. The behavior (conservatively treating the returned temporary as unsafe to return by reference) is correct; the parenthetical says the opposite and will mislead the next editor of this recurring-bug-class code.
- **Evidence**: compatibility.py:2146-2148: `# Expression callees return temporaries (not dangling)\n if not isinstance(expr.func, TpyName):\n     return True`
- **Fix direction**: Fix the comment to 'returns a fresh temporary -- dangles if returned by reference'.

#### S186. _body_has_raise misses `with` bodies -> false '__next__ has no raise StopIteration' warning

- **Location**: tpyc/sema/analyzer.py:171-198, tpyc/sema/analyzer.py:2487-2493
- **Severity / category**: low / slop -- reproduced: yes -- found by `sema-core`
- **Problem**: _body_has_raise recurses into If, While/ForEach, Try, and Match but not TpyWith, so a `raise StopIteration` inside a `with` block in __next__ is invisible and the 'iterator will loop forever' warning falsely fires. Sibling-gap of the same whitelist-recursion pattern as the __init__ walk_body finding.
- **Evidence**: next_with.py: __next__ with `with Ctx() as c: if self.i >= 3: raise StopIteration; ...` -> warning: "__next__() has no 'raise StopIteration' -- iterator will loop forever if caller exhausts it" despite the raise being present (verified via --dump-code).
- **Fix direction**: Use the generic stmt.sub_bodies() accessor for recursion instead of the isinstance whitelist (same fix shape as the __init__ walk_body finding).

#### S187. Bounds-safe / div-zero side-table facts keyed by (line, name) collide for multiple same-line accesses

- **Location**: tpyc/sema/expressions.py:1330-1336, tpyc/sema/expressions.py:1345-1351
- **Severity / category**: low / slop -- reproduced: no (code-read evidence) -- found by `sema-expr`
- **Problem**: subscript_bounds_facts and div_zero_facts are keyed by (loc.line, obj/divisor name). Two subscripts of the same object on one line with different indices (`xs[i] + xs[j]` where i is proven in-bounds and j is not) overwrite each other; last write wins. The per-node `expr.bounds_safe` / `expr.divisor_non_zero` flags are what codegen consumes, so emitted code is correct -- but the side tables feed `# tpyc:` annotation validation, which can assert against the wrong access. Also a textbook example of the id/line-keyed side tables the THIR migration notes say to avoid (the fact already lives on the node; the table duplicates it lossily).
- **Evidence**: expressions.py:1336 `self.ctx.subscript_bounds_facts[(expr.loc.line, obj.name)] = is_safe` and :1351 `self.ctx.div_zero_facts[(expr.loc.line, right.name)] = is_safe` -- dict write keyed only by (line, name), executed once per access on that line.
- **Fix direction**: Key by (line, name, col) or accumulate per-key lists; or drop the table and have annotation validation read the per-node flags.

#### S188. LiteralType equality is value-order-sensitive

- **Location**: tpyc/typesys.py:691-702
- **Severity / category**: low / slop -- reproduced: yes -- found by `typesys`
- **Problem**: LiteralType stores `values` as an ordered tuple with default dataclass equality, so Literal['r','w'] != Literal['w','r'] even though typing semantics treat Literal members as a set. Two spellings of the same annotation (e.g. across a protocol signature and an implementation, or in overload-signature comparison) will fail equality and any set/dict dedup. Low impact today because Literal is mostly used for overload dispatch against single-value enrichment literals, but it is the same interning-vs-structural-compare bug class as the union findings.
- **Evidence**: LiteralType(STR, (r, w)) == LiteralType(STR, (w, r)) -> False (verified via direct construction).
- **Fix direction**: Canonicalize the values tuple (sort by (tag, repr)) at construction, mirroring make_union's intent.

#### S189. is_trivially_destructible docstring contradicts behavior for Span

- **Location**: tpyc/typesys.py:424-432
- **Severity / category**: low / slop -- reproduced: yes -- found by `typesys`
- **Problem**: The docstring claims the method is 'Conservative for non-value types (always returns False even if the C++ type is actually trivially destructible, e.g. Span)' -- but Span's TypeDef declares is_value_type=True, so Span returns True from this method (verified). The result happens to be correct (std::span is trivially destructible), but the comment misclassifies Span as a non-value type and misdescribes the method's behavior for it; a future reader extending the conservative branch would be misled.
- **Evidence**: make_span(INT32).is_trivially_destructible() -> True; make_span(INT32).is_value_type() -> True; docstring at typesys.py:428-431 says Span 'always returns False'.
- **Fix direction**: Fix the docstring example (use list/dict as the conservative non-value example).


## 9. Refuted / disputed findings

- **Own[T] param consumption is not recognized inside generator bodies (copies + spurious 'never consumed' warning); async handles the identical code correctly** (`hunt-own-moves`): The finder's repro pair is NOT byte-identical: in genown.py the generator reads p again AFTER k.take(p) (a second 'yield len(p.vals)'), so the take is not a last use -- auto-move would be wrong, and the 'copies P into owned storage' warning is the designed diagnostic for that copy; the finder's async body (asyncown.py) has no post-take read, so take IS the last use and moves. Symmetric controls prove no generator/async asymmetry exists: (a) /tmp/agents/verify/asyncown2.py (async WITH a post-take read 'return len(p.vals)') produces the IDENTICAL two warnings ('copies P into owned storage' +...

One finding received conflicting verdicts across the two verifier invocations (an infrastructure resume re-ran some verification chunks): "Borrow-form types report Send of their referent" was first refuted (behavior is the documented contract) and finally adjusted to a low design note (the type-level `is_send` has no pointer-repr gate, but the enforcing layer `sema/frame_traits.py` is conservative at every slot kind, so no unsoundness today). It is listed under Design concerns with that caveat.


## 10. Coverage bounds (what was NOT exhaustively reviewed)

Every reviewer declared what it skimmed, sampled, or skipped. Condensed per area -- treat these as the audit's blind spots:


### Round 1

- **parser**: Did not read tpyc/frontend_ir/__init__.py beyond noting it is re-exports (140 lines, not inspected line-by-line). Did not trace sema/codegen consumers for the loop-var-shadowing and asyncio.run-shadowing findings past the repro (their roots extend outside my assigned files; flagged for gap-sweep). Did not exercise multi-level package imports (import a.b.c attribute resolution), relative-import placeholder resolution across packages, or the macro FragmentParser at runtime -- reviewed by reading only. type_resolver's generic-alias and recursive-union paths were reviewed statically, not run (the line-986 NameError finding is static evidence only). Did not audit dump_types.py, module_names.py...
- **typesys**: Did not audit the sema/codegen consumers of these modules except targeted excerpts (sema/overloads.py:190-250, sema/calls.py:2700-2740, sema/methods.py:230-250, sema/expressions.py:3390-3420) read to assess reachability of the dict-view element bug; sema/type_ops.py's substitute_type_params (the 'more capable' substitution) was not reviewed -- force_pointer_repr drop sites may exist there too. Send/Sync enforcement sites (frame_traits.py, marker conversion checks) not traced, so the borrow-form is_send finding is flagged low-confidence for gap-sweep. C3 linearization, protocol-conformance machinery, and resolver case-sensitivity logic were read but only lightly adversarialized. Did not te...
- **sema-core**: Supporting files only sampled where needed: flow_facts.py (merge policies), statements.py (expr_yields_non_null_ptr consumers, var_decl_by_name writes, loop-entry call sites), local_deduction.py (retro-widen write site). Did not audit: protocol conformance internals (protocols.py), method/call/expression analyzers, mutation_propagation fixpoint, macro/builder-trace expansion paths, the cross-module skeleton-adoption (_adopt_skeleton) machinery beyond reading it, or codegen. Speculative candidates I noted but did not chase for time: macro-added Final class attributes bypassing _partition_class_constants (partition runs pre-macro); lambda_scope saving only definitely_assigned (narrowing lea...
- **sema-stmt-flow**: Did not fully read sema/match.py beyond the exhaustiveness/missing-cases region, nor the codegen_cpp/expressions.py bounds/div emission internals (verified only the sema flags and the emitted C++ differences). Did not exhaustively audit the FlowFacts merge of every hazard set (owns_fresh_tuple_member_vars, borrow_into_own_hazards, etc.) -- spot-checked merge policies (consumed_vars UNION vs current_consumed_own_params INTERSECT are both individually correct). The liveness 4-iteration bounded fixpoint (_analyze_while/_analyze_for_each) is a real under-iteration soundness concern for very long loop-carried def-use chains but I could not construct a triggering use-after-move within typical n...
- **sema-expr**: Codegen files (codegen_cpp/expressions.py, types.py, records.py) and narrowing.py were only spot-read to trace reproduced sema findings to their root, not audited. Did not audit: calls.py/methods.py dispatch (operator-adjacent but out of scope), compatibility.py coercions, protocol conformance internals, overloads.py matching tables, prescan/local_deduction (the nested-list mutation root lives there; flagged for gap-sweep). Untested edge areas I read but did not repro: reflected-operator precedence for subclasses (Python tries right.__radd__ first when right subclasses left; TPy is always left-first), `x in (1, "a")` mixed-tuple membership rejection (CPython allows), `is` rejection on two...
- **sema-calls**: Did not read sibling modules these files call into (type_ops.py inference internals, methods.py method-call path, expressions.py, protocols.py, coercions.py, local_deduction.py) beyond their call signatures -- method-call analogs of the reported call-path bugs (e.g. __call__-dunder routing, method overload resolution, receiver filters) are unverified and flagged for the gap-sweep. Algorithmic-cliff review was read-level only (no profiling): pass-1 candidate pools run inference once per generic overload and Regime C re-analyzes lambda bodies per candidate (documented v1) -- no accidental quadratic found, but I did not benchmark large overload sets. Tuple-hazard / dangling-return machinery...
- **sema-methods-protocols**: Did not read calls.py / overloads.py / expressions.py / statements.py in full (only greps + targeted excerpts), so mutation-edge recording for direct Callable locals/params, function-value edges, and overload-resolution internals were probed only behaviorally. Hypotheses tested and DISPROVEN (no finding filed): protocol-to-protocol conformance ignoring type args (Iterator[Int32] vs Iterable[str] correctly rejected); narrowing leak between/after match arms (correctly rejected); @readonly method mutating through a self field (correctly rejected). Not pursued: scan_by_short_name protocol short-name collisions across modules (plausible misresolution, untested); enum-value-pattern exhaustivene...
- **codegen-expr**: Did not exhaustively adversarial-test: tuple-literal slot-mode logic beyond the double-generation repro (the borrow/storage slot decisions at 5377-5477 were read but not stress-tested), generator-expression/comprehension emitters (read, lifetime logic spot-checked only), lambda capture correctness, narrowed-vars save/restore interplay across nested conditions, and the `in`-operator with indirect reference-type LHS (gen_expr without deref at expressions.py:1617/1637 looked suspicious but I could not build a plausible failing case in time). Evaluation-order divergences weaker than the reported ones (print file= ordering, TypedDict .get comma-operator default-before-receiver at 3172, make_or...
- **codegen-stmt-match**: Did not audit: the resumable-frame (generator/async) arm-routing and frame-field paths beyond reading them (no yield/await repros); string-switch discriminator bucketing correctness beyond reading; expression-level helpers in expressions.py/context.py consulted only where statements/match call into them; sema root causes of the PendingListType-in-try and list-swap bugs (located the crash sites, did not trace why sema leaves the type pending / creates the alias cycle); _gen_if's isinstance-extraction state machine read carefully but not adversarially repro-tested; @overload-specialized paths and macro-emitted elif chains read but not executed. Verified two narrow sema slices of sema/match....
- **codegen-funcs-records**: Did not trace into statements.py/expressions.py/gen_async/gen_generators bodies (outside assignment) -- findings 6 and the secondary union-narrowing error in finding 5 have root causes there and are flagged for the gap sweep. Skimmed rather than adversarially probed: _overload_stubs_are_literal_only short-arity zip truncation, the Adapter unconstrained forwarding ctor hijacking copy construction, _qualify_user_types regex edge cases (param name equal to a record name), is_lvalue_iterable/is_rvalue_source classification tables, the comment-emission helpers in context.py (cosmetic-only), and Throwable auto-emit clone() on records with nocopy fields. Finding 10 (param_const divergence) is co...
- **codegen-orchestration**: Protocol/record emit-ordering machinery in generator.py (_collect_protocol_deps, forward-decl cascades, recursive-union wrapper placement, re-export using-decl paths, fwd-header cycle handling) was read but not adversarially probed with multi-module/mutually-recursive-type repros -- BUGS.md:68 already tracks the main known ordering hole there. Not exercised at runtime: cancellation paths (poll_with_cancel, CancelledError-at-suspension), MatchDispatch resumable emission, templated/generic coro emission, async-for else (parser-rejected), multi-item async-with (builder-rejected), nested CFG-finally forwarding, and the _compute_case_entries deep-dataclass-equality region comparison (judged a...
- **orchestration-cli**: Did not audit non-assigned modules beyond the targeted slices above (sema/, codegen_cpp/, parse/, modules/resolver.py, explain.py, macro_loader.py, frontend_ir/) -- the cross-module mutation-fact finding therefore only proves the readonly-method-call consumer is stale for cycle peers; other Phase-2-fact consumers (auto-move, Send/Sync, mutation-based dispatch) were not traced and are flagged for the gap-sweep. REPL link-flags/runtime-sources finding (import re / import socket in REPL) is code-evidenced but not runtime-reproduced because the include-path bug breaks every REPL build first. Did not exercise frontend plugins end-to-end (pascal), --install-agent-docs file IO, generate_cmake ou...
- **macros**: Did not build/execute any C++ binaries (dump-code only; the dangling-view UB is asserted from the emitted C++, not an observed crash). Skimmed but did not deep-audit: lib/tpy/tplib/json/model.py and the full dataclasses macro bodies (macro *consumers*), sema/builder_trace interactions with the analyzer's pass ordering (pass 5.5 placement taken on faith from docstrings), the FragmentParser quoting path internals, and ClassInfo generic-record (type_params) handling under macros. Did not test: function macros end-to-end via a frontend plugin (no in-repo @function_macro fixture located quickly), Final-literal resolution stubs (documented as pending), the re-export/attribute-chain resolution p...
- **hunt-own-moves**: Skimmed only: ESCAPE_ANALYSIS_DESIGN.md and OWNERSHIP_PROBLEM.md (headers/status tables; full escape-analysis machinery for Ptr[T] provenance was out of move-semantics scope), codegen_cpp/statements.py move-emission sites (del-sink, return moves -- spot-read around grep hits, not line-by-line), codegen_cpp/records.py constructor Own-param handling (trusted existing auto_move_ctor tests), sema/match.py consumption merging (trusted error_consuming_method_* tests), gen_generators.py/resumable_cfg.py internals, consuming-iteration (own_iter/auto-consuming for-loop) beyond the sema validation read, Box/Rc library types, mutation_propagation call-graph fixpoint. Did not test: move-state across...
- **hunt-borrow-storage**: Skipped as already tracked in BUGS.md: nested-tuple recursion (BUGS:56/239), readonly-tuple/element variants (BUGS:30/40/45/58/70), Own-slot moves (BUGS:24/44/54), value-variant union returns (BUGS:51), Optional generator-yield narrowing (BUGS:251/279), dict.get dangle (BUGS:146), match on Optional[A|B] (BUGS:180). Did not probe: closure capture of borrow forms (blocked by 'lambda parameter types cannot be inferred' restriction -- a Callable-context repro was not attempted), async generators / async with / context-manager boundaries, set-element and dict-key boundaries beyond reads, generic (TypeParamRef) instantiations of the broken shapes, frontend plugins, @native interop ABI, @error_r...
- **hunt-silent-copy**: All probes are sync free-function bodies: async/generator (resumable-frame) variants of every boundary were NOT probed (BUGS.md already tracks several frame-slot copy bugs there; my items()/match/walrus findings need separate verification in frames). Not probed: set-element aliasing of custom objects, exceptions (except-as binding), @nocopy/Own move-semantics correctness, Box/Rc/Weak, native interop, string/bytes (immutable, divergence unobservable), match sequence patterns and star-unpack (both rejected as unsupported -- not investigated against docs), multi-module/import boundaries, escaping closures (tracked). Compiler source read was targeted to root-cause confirmation only -- no syst...
- **hunt-dangling-views**: Did not exhaustively map every str method's view-vs-owned return classification (confirmed strip/slice return views, upper returns owned and is handled correctly). Did not reproduce the view-across-yield/await suspension or closure-capture variants -- flagged as untested sibling gaps in finding 1 rather than reproduced. Did not audit dict/set view provenance or @native-returned views. The Span inline-literal dangling (finding 3) is masked by a separate codegen compile error so its UAF could not be observed at runtime; the .copy() form (p.py) confirmed the Span family dangles. Did not read codegen_cpp emit sites line-by-line for the temporary-hoisting logic that makes call-arg list literal...
- **hunt-readonly-narrowing**: Skimmed only (not adversarially audited): sema/methods.py readonly receiver resolution beyond the lines grepped, mutation_propagation.py fixpoint, codegen param_const.py, codegen variant-extraction/isinstance emission (several stale-narrowing codegen bugs already tracked in BUGS.md 297-305), @auto_readonly clone machinery, protocol/dynamic-protocol readonly contracts, match-statement narrowing (BUGS.md 100/181/296 already cover gaps), resumable-frame/generator/async narrowing-across-suspension (BUGS.md 297-299), Span/Ptr readonly propagation (only via grep of expressions.py 1798-1804/3499-3504), set/bytearray readonly, with-statement and finally-tier fact handling (except-throw tier prove...
- **hunt-perf**: compatibility.py only skimmed at function-map level (per-pair structural recursion, nothing program-size-quadratic spotted, but not line-audited); narrowing.py not read line-by-line (its branch-merge layer is covered via flow_facts/init_tracker findings); generator.py emit-ordering loops grepped for nested sweeps only; async/generator resumable-frame codegen not audited in depth beyond grep hits in async snapshots; match-statement flow merges noted as sharing the save() pattern but not separately stress-tested; builtin overload-set sizes not enumerated; no 100k-line end-to-end compile was run (largest synthetic ~10k lines, extrapolation from measured x4-per-doubling); snapshot review was...
- **hunt-slop**: Did not line-by-line read the ten largest modules (expressions/statements/calls/typesys/analyzer, ~45k lines combined) -- coverage there is via the automated scans plus targeted reads, so dead BRANCHES inside live functions (beyond what ruff/SIM114 catches) and stale comments referencing still-existing-but-repurposed symbols are under-sampled. Skipped: test_*.py entirely; lib/ and runtime/ comment hygiene; the 20 long comment blocks flagged by my block scan (sampled two -- codegen protocols docstring, third_party section -- and judged the rest likely legitimate design rationale, not individually audited); dataclass-field liveness (unused fields on TpyType/FunctionInfo etc. not checked); m...

### Round 2 (gap sweep)

- **gap-siblings**: Did not build/run any binary (all evidence is generated-C++ inspection per the dump-code-first rule); never ran pytest. Skipped or only partially traced: set-literal copy warning and set/dict-comprehension value copies (likely share the dict-literal gap); _build_async_with exception-in-__aenter__ at runtime (structure looked correctly nested, not executed); overload-resolution sibling traces (kwarg gap-fill, readonly-strip ambiguity) -- too slow to repro reliably; borrow/storage container-store siblings for dict/set (round-1 subscript-assign path likely shared); liveness/auto-move siblings beyond round-1 (subscript aliases share the cited prescan lines, so not re-reported); REPL, macros,...
- **gap-coverage**: Adapter unconstrained forwarding ctor (codegen_cpp/protocols.py:786-787) confirmed present and a genuine C++ hijack-the-copy-ctor pattern, but I could not construct a reachable generated-code site that copy-constructs an Adapter from a non-const lvalue (dyn-protocol values route through nocopy Box/RefAdapter), so not filed. Async siblings asserted but not separately built: dict.items() unpack with await in body (shares the resumable-frame emission of finding 2) and const-catch in async try/except (statements.py helper explicitly shared per docstring). Did not probe: multi-module variants of any finding, macro/builder-trace paths, REPL, @native interop, match-statement analogs of the `in`...
- **gap-exceptions-with**: Did not audit: the CFG-based (suspending) finally internals in gen_async.py (M3.3.x pending-return/exception-slot machinery -- only black-box tested via the async finally exception-path run, which passed); asyncio cancellation paths and Executor exception storage (covered by existing BUGS.md entries); runtime C++ headers for the Throwable ABI / what()/clone() emission; @native exception interop; match-statement + exception interactions; except-handler dead-ordering (subclass after base) C++ warning behavior; the GCC statement-expression goto-out-of-larger-expression temporaries question for @error_return unwrap in argument position (potential UB/leak class, left unverified -- flagged here...
- **gap-strings-bytes**: Skimmed only: FStr macro decomposition (as_fstring/builder-trace path) -- read the design doc but did not exercise log-macro codegen; REPL string handling; @noalloc interactions (documented as Planned); str.format/%-formatting (% is rejected with a clean 'Invalid operand types' error -- noted, not filed); explicit String type paths beyond param shape; Final[str] constexpr emission; f-string format-spec parser validation matrix (spot-checked BigInt+spec rejection only); async/await siblings of the yield-view hole (generator sibling confirmed; async assumed same family, flagged for gap-sweep); exceptions __str__ -> StrView escaping except blocks (flagged in finding 2, not reproduced); bytes...
- **gap-enum-union-match**: Did not audit: sema/narrowing.py internals (isinstance fact merging, while-isinstance, tuple-form isinstance) beyond behavioral probes; enum codegen emission code itself (records/generator.py side) -- keyword finding evidenced from output only; recursive-alias finalize pass and wrapper templating internals (behavioral probes only -- alias match/aliasing/Optional-of-wrapper probed OK); resumable (generator/async) match arm routing; polymorphic dispatch paths (read but not probed -- BUGS.md already tracks several); string-switch dispatch internals; exhaustiveness-warning sema details (test corpus covers); sema/compatibility.py union rules; Own[union] move semantics; @nocopy unions; cross-mo...
- **gap-bigfiles-second-pass**: Did not line-by-line read all ~11.8k lines of the two files. In expressions.py I skimmed or skipped: the isinstance lowering bulk inside _gen_call (read once, not adversarially probed), _gen_method_call (3149-3718) beyond what repros exercised, the dynamic-protocol/native arg helpers (781-960), subscript/slice emission (5562-5760), and tuple-literal internals (round 1 covered). In calls.py I skipped the dyn-attr builtins (1219-1435), isinstance analysis bulk (1702-2320), template-constructor path (3019-3234), regime-C Fn-slot resolution (3676-4116), and the call-macro chain (5446-5580). Combos considered but not run: await in f-string/assert variants (round 1 covered the desugar gaps), ge...

### Global bounds

- The audit covered `tpyc/` at file granularity with two independent rounds, but the largest files (`codegen_cpp/expressions.py`, `sema/calls.py`, `codegen_cpp/statements.py`, `typesys.py`) are 4-6k lines each; reviewers prioritized high-risk paths within them. Dead branches inside live functions are under-sampled (the slop hunter relied on automated scans plus targeted reads for the ~45k lines of the ten largest modules).
- Out of scope by design: runtime header correctness (except where the compiler's emission contract is the bug), stdlib breadth (`tplib/`, `re`, `socket`, JSON, asyncio runtime internals), `lib/cpy/` stubs, the Pascal frontend, `@native` interop depth, Box/Rc/Weak semantics, Send/Sync enforcement depth, the `Ptr[T]` escape-analysis machinery (documented TODO), and build/vendoring code beyond `tpyc/build/`.
- Not run: the pytest suite (per audit rules), ASAN/UBSAN builds, any 100k-line-scale compile (perf-cliff findings are complexity arguments from code reading, not benchmarks).
- Findings were not exhaustively cross-checked against `BUGS.md`; reviewers checked opportunistically. Known overlaps are flagged in section 3 of the narrative.
- Evidence fields in this report are trimmed to ~900 chars; full repro transcripts live in the workflow transcript JSONL and `/tmp/agents/` (ephemeral).
