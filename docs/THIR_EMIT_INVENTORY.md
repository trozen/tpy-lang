# THIR Emit-Arm Inventory

> **Operating model (2026-07-12):** the goal is COMPLETION (deleting the AST
> codegen), not a permanent hybrid -- and the direction is now the zero-whole-body-
> fallback loop in CLAUDE.md "THIR migration". The smallest per-construct residual
> prioritizes a dependency cluster; it does not authorize deleting an individual
> AST arm. The emit-arm inventory, leverage tables, and final deletion targets are the current
> per-construct what-to-port reference. The fan-out/wave *sequencing* plan and
> the "maximize throughput to near-100%" framing describe the earlier
> coordinated campaign and are no longer how the work is driven.

The map for completing the THIR migration (deleting the AST codegen path).
Drives the work by the **finite emit surface we must port** (~236 emit functions
/ ~380 dispatch arms / ~36k lines in `tpyc/codegen_cpp/`), not by the
combinatorial ~5,100 body *shapes* those arms generate. Shapes are the products;
arms are the generators. Chasing shapes is asymptotic; porting arms is bounded.

Companion to `THIR_COMPLETION_LEDGER.md` (deletion-target model)
and the shape meter (`tpyc/thir/shape.py`, `$THIR_SHAPES_JSON`) which measures
progress. Leverage figures below are real-corpus blocked-body counts from a full
`--thir-codegen` run (2026-07-06, master @ edc226168e; 691/5136 distinct shapes
routed). **Updated 2026-07-08 (branch thir-param-grid):** ~1,239/5,179 distinct
shapes routed (~24%) after the 8 param/return/compositional waves. Note: the
COMPOSITIONAL-GATE approach (a single "type renders identically AND sub-exprs
route" predicate for type-keyed constructs -- container params, for-each
elements, comprehension loop vars, subscript value-leaf reads) reduces the
effective arm count for those constructs from a per-family whitelist to one
predicate; form-sensitive constructs (call-args, field-writes, container-literal
element storage) do not collapse until the form fact is on the node.

**Tally-honesty correction (2026-07-06, container-rung branch):** the fallback
attempt driver used to count (and sometimes route) `...`-stub callables the AST
never gen_bodies -- declaration-only stubs (`cast`, `isinstance`, native-class
method stubs) and bare-`@native` methods (`native_name` still None at codegen).
Excluding them (`is_stub` in `_is_bodyless_binding`) shrank the routed tally
~261k -> ~36k and body fallback ~200k -> ~143k; the DISTINCT-SHAPE meter barely
moved, confirming it was the honest dial all along. Body-count leverage figures
in this doc predate the fix and over-state per-case stdlib mass; headline items
now known to be a handful of real bodies repeated per case: `sig.receiver_record`
~17k = Poll's 5 bodies + Waker (builtin-receiver family), `sig.param_type`'s
async plumbing ~8k = Waker/None-typed slots. Honest post-fix sig totals:
`sig.param_type` 20,030 (582 shapes), `sig.return_type` 6,721 (157 shapes).

## Two hard findings up front

**1. Under per-body routing, no AST emit function deletes until near-100%.**
THIR interception is per-BODY (a body routes wholly through THIR or wholly falls
to `gen_body`). So `_gen_if_expr` keeps a live caller as long as *any* fallback
body anywhere contains an if-expr -- which is true until nearly every body
routes. The dispatchers (`gen_expr`, `gen_stmt`) and their helpers therefore
delete only at the very end. **"Sequence by deletion for early payoff" does not
work here** -- deletion is a near-all-or-nothing gate at the end of the curve.
The fastest path to B is therefore *maximize throughput to near-total coverage*,
not "delete components early."

**2. The ctor-MIL target is LATE, not near-term** (corrects the ledger's
"closest deletion target"). `_extract_field_inits` logic is fully ported, but
its lone caller only goes dead when EVERY record's ctor routes. Wave-6 closed
the param form-rungs (`ctor.param_type` residual ~294: Waker/protocol) and the
flat F3+ field form-rungs; the remaining gates are the generics frontier
(`ctor.non_f1_record` + the `UninitStorage[T]`-family MIL mass inside
`ctor.mil_field`), async plumbing (`Waker`), AND full body-statement coverage.
It lands with the rest, not before.

## The work in four buckets

Ranked by leverage; P = parallelizable (disjoint type-family cells, worktree
fan-out), S = serial (shared machinery / design-heavy, one main thread).

### A. Signature type-family grid -- the biggest mass [P]

Gate-widening in `_f1_param_eligible` / `_eligible_return` / `_ctor_param_eligible`
to admit more type-families in param/return/ctor-param position. Disjoint per
type-family -> the primary parallel-worktree lever.

| Gate | leverage | distinct shapes | missing type-families |
|------|---------:|----------------:|-----------------------|
| `sig.param_type` | 20,030 | 582 | Waker/async plumbing (~8k, deferred), tuple, union, Own[non-record incl. Own[container]], protocol, generic-element containers `list[T]` (scalar/record/opt/ptr/container-scalar/span done) |
| `sig.return_type` | 6,721 | 157 | tuple residue (~3.4k pre-value-tuple-rung; the remainder is non-value element families: record/nested-tuple/view elements), protocol/own:protocol, Waker, own:union (scalar/str/opt/borrow-tuple/record/Own[record]/Own[container]/Own[str]/Own[bytes]/ptr done) |
| `ctor.param_type` | 294 | -- | DONE (wave-6 grid-fill: Own/String/Ptr/union/optval); residual = Waker + protocol/Callable params |

(Counts re-measured 2026-07-06 on the container-rung branch, post
tally-honesty fix: storage container returns (`Own[list/dict/set]`, bare-name +
literal sources), `Own[str]`/`Own[bytes]` returns, and `Span[scalar]` /
`Span[readonly[scalar]]` params landed. Ctor rows re-measured 2026-07-07
post wave-6 (the ctor frontier): `ctor` component 17,604 -> 12,025 (the
wave-6 pre-foundation baseline had `ctor.param_type` at 4,116 -- the 4,395
above was the older 2026-07-06 container-rung measure); `ctor.mil_field`
residual 8,616 is mostly generics-gated (`UninitStorage[T]` fields,
`Box._ptr = heap_take(...)`) + `Waker`; see IR_DESIGN.md Wave-6. The
2026-07-14 ctor-MIL cells landed value-repr `optional.name` bare-copies,
`callable.name` bare-copies, and `tuple.tupleliteral` via `THIRRecordCopy`
+ `tuple_to_storage`; deferred: genrec fields (the `_f1_record` generics
policy), `base_init`, `callable.lambda` -- see the TODO.md ctor-MIL entry.)

### B. Expression arms feeding var-decl / return / if / expr-stmt [mostly P]

Most statement "leverage" is really the expression init/condition residue
surfacing at the statement (var-decl/return/expr-stmt/if structures are already
ported). The arms, by `gen_expr`/`gen_method_call` dispatch:

| Arm | THIR admission / AST locator | status | biggest gaps | leverage | P/S |
|-----|----------|--------|--------------|---------:|-----|
| `TpyMethodCall` | expressions.py `_lower_expr` method-call arm | partial | `method.marker` module-qualified + static receivers (wave 2) + static `@cpp_template` calls + Ptr-receiver deref calls (wave 3) + set receivers (`_set_method_recv`, raw-param-typed stub args) + qualcall F1-record RVALUE storage returns + free-call-result receivers + zero-args-all-defaults marker calls (call-cascade wave) LANDED; measured residue: `deref.ptr_pointee` 939 (Ptr[@dynamic-protocol] pointee family), `deref.recv_shape` 637, module/static generics ~226 (generics frontier), qualcall coerce-args 304 + optional returns; **str/bytes methods** (receiver not a nominal record -- new receiver-family dispatch) ~1.7k; non-F1 field receivers (~35, needs a drill); partial-defaults arity; multi-overload; non-value returns | ~8k | P (grid) + S (str-family machinery, non-F1 receivers) |
| `TpyCall` | expressions.py `TpyCall` arm (`_call_use_supported`) | partial | non-scalar returns `call.ret_type` 2433 (grid-fill, return-slot); imported/cross-module/error_return/generic callees `call.callee_kind` 2087 (new machinery); `call.special_form` 1084 (bespoke cast/isinstance/enum/macro arms) | ~7k | mixed |
| `TpyBinOp`/`ChainedCompare` | expressions.py `_lower_expr` binop arm | partial | mixed/widening arith (coercion node), Char concat, record operator-dunder indirection, non-bool logical (temp+ternary machinery) | ~3.9k | P (grid) + S (non-bool logical) |
| `TpyFString` | expressions.py `TpyFString` arm | done (wave 2) | `!r`/`!s` + constant format specs landed; unmirrored arg types under conv tagged `fstring.arg_wrap` | landed | P |
| `TpyName` global read | expressions.py `_lower_expr` name arm | done for value globals (waves 2-3) | seeding + Ptr[T] writes + native/imported spelling (THIRName.cpp via the extracted imported_variable_cpp) landed; remaining: non-value pointer-slot indirection, Optional-value globals (BUGS.md miscompile), module-attr dotted reads (a field-access render path) | residue | P |
| `TpySubscript` | expressions.py `TpySubscript` arm | partial | field-access receivers landed (wave 3, all four ops); remaining: non-value element reads (`subscript.elem_family` 321 -- the W2-subscript-elem cell), deeper receiver chains, `len(field)` / field iteration / bytes-field reads | ~400 | P |
| `TpyFieldAccess` | expressions.py `_lower_field_source` | partial | non-value field reads (record/container/opt/union field), non-F1 receiver | ~1k | P |
| `TpyIfExpr` (ternary) | expressions.py `TpyIfExpr` arm | done (wave 2) | `THIRIfExpr` for scalar/Char/enum/str results; non-value/bytes results tagged `ifexpr.result_type` (first-rejects 1073 -> 18) | landed | P |
| list/dict/set literal (value position) | expressions.py literal arms + checks.py `_container_literal_decl_ok` / `_container_literal_shape_ok` | partial | decl-init + print-arg + CTOR list-literal args (brace-init in place) + free-call arg positions (list-literal -> hoisted `__tmp_N` ref-param temp, wave 15) today; dict/set-literal ctor args, value/return positions + non-scalar elements missing | ~700 | P |
| `TpyTupleLiteral` | expressions.py `TpyTupleLiteral` arm | partial (value-tuple rung) | `THIRTupleLiteral` for all-VALUE scalar/owned-str elements at returns/decls/args; VALUE-capture non-value (record/`Own`) elements land as storage-form decls (`_storage_record_tuple`); rvalue non-value tuple elements in CONTAINER literals route via `tuple_to_storage<S>(S{...})` (scalar/str/rvalue-record members, `_container_lit_elem_ok` tuple arm); lvalue/borrow ref-capture elements still stay AST | tuple residue | P |
| `TpyListRepeat` `[0]*n` / walrus / lambda / genexpr | 4857/6514/6816/5170 | partial (lambda + list-repeat) | lambda routed (`THIRLambda`: by-ref `Fn` + by-value `Callable` capture, non-void body); list-repeat routed for the materialized-list + `Array[T,N]` shapes (`THIRListRepeat`, wave 16; lazy `ListRepeatType` still AST -- protocol-arg pending-type crash, see TODO); walrus / genexpr still unlowered (low-frequency; genexpr = serial) | low | walrus S |

Fully ported expr arms (no work): int/float/bool/str/bytes literals, `len()`,
scalar ctors, slice ctors, enum lookup, the arg-temp/move/union-lift/optional-ptr
call-arg machinery.

### C. Serial control-flow subsystems -- the completion floor for B [S]

These can't be parallelized and set the timeline. Front-load their design.

| Subsystem | status | machinery still needed | leverage |
|-----------|--------|------------------------|---------:|
| **generator + async** (`yield`/`await`) | partial (foundation + wave 2, 2026-07-07) | APPROVED DESIGN (supersedes "ported into THIR"): the state-machine SKELETON (resumable_cfg + gen_async: CFG, frame struct, case labels, region replay, suspend/resume) stays SHARED machinery for both paths -- the structural-emission precedent + IR_DESIGN MIR OQ6 (state-machine transform remains a codegen concern); only the LEAVES route through THIR via a seam at the skeleton's delegation sites (`lower_resumable` / `THIRResumableBody` / `ResumableLeafEmitter`, component `resumable`, reasons `res.*`). LANDED on branch thir-async-frontier: foundation (free async defs, value-scalar) + wave 2 -- R2 (instance-method coros), R4/R4b (value-scalar generators incl. methods), R1 (non-value locals: str/bytes bare + frame_slot records/containers), R5b (bound-method await receivers), R5c-param (F1-record params). LANDED on branch thir-generators: the simple-generator lambda PEEPHOLE's leaf seam (`lower_simple_generator` / `THIRSimpleGenBody` / `SimpleGenLeafEmitter`, reasons `sgen.*` -- value-scalar yields/loop-var elems, methods via `(*this)`; the lambda skeleton stays AST). REMAINING cells in TODO.md ("Resumable frontier"): str/Own/tuple/union params, non-value yields/returns (shared with the peephole's `sgen.yield_type`), generic-record methods, regions (try/except/finally/with), loops, ERASED/BORROWED awaits, R7-R9 | ~9,600 (`sig.async` 7739 + `sig.generator` 1876) -- **largest serial rock** |
| `TpyForEach` | partial | `__iter__`/`__next__` protocol loop LANDED for free/member/module-qualified generator-call, NON-generator iterator-returning call (user-iterator ctor/factory, Iterator/Iterable protocol returns -- wave 2), and concrete user-iterator-name iterables (`THIRForIterProto`); scalar/str tuple-unpack heads over the proto loop LANDED (wave 2); still open there: self/field/protocol-typed/generic sources, gen-valued locals; async-for (shares resumable frame); for-else `goto`; consuming/hoist-loop-var; borrow-tuple unpack targets LANDED (standalone `a,b=it` + for-head `for a,b in items` over a storage container: value/cref/ref binds through the `tuple_to_pointer` lift, const-source `const T*` for const-ref params / readonly `self.field`, iter-proto sources defer); remaining tuple-target rungs: hoisted-ref, full const-source mirror (const container local / view-call), subscript-source realias (enum-source iteration `for c in Color:` ROUTED, `foreach.enum`) | 2,005 |
| comprehensions (list/dict/set partial, genexpr none) | partial | nested-container ELEMENTS landed (list-of-list / array-of-vector via `_lower_comp_container_elem`: container literal / nested comprehension element, typed-brace dict-value only); still open: other value-position sinks beyond decl-init/print-arg; genexpr | moderate |
| `TpyNestedDef` (closures) | partial | resumable-body, generic/error_return, default-param, Optional/Own/value-opt params, self/narrowed captures, name collisions still reject | 12 (rare) |
| `TpyWith` | partial | async-with (resumable); non-F1/temp-registering managers | ~50 |
| `TpyTry` | partial | return tier ROUTED (goto dispatch + `__err_opt_N` capture + finally wrap; see test_thir_error_return.py); residue: non-value hoists, in-branch/in-loop fresh predecls | ~10 |
| `TpyMatch` | partial | richer pattern set beyond the admitted slice | ~50 |
| `TpyWhile` | partial | while-else `goto`; folded `while True` | ~950 |

### D. Cross-cutting non-F1-record / generics rock -- one serial unlock [S]

A single frontier gates a huge, dispersed leverage mass because non-F1 records
(generic / cross-module / native) are rejected wholesale by `_f1_record`:

| Reason | leverage | note |
|--------|---------:|------|
| `sig.receiver_record` | 31,049 | mostly BUILTIN-type methods (str/list/dict/...) -- **bodyless, out of scope** (emit via specialization/native, never `gen_body`); only ~7 distinct shapes |
| `method.marker` | 8,957 | method-call special markers + non-F1 receivers |
| `call.native_arg.record_nonf1` | 6,446 | non-F1 record into native/template arg slot |
| `ctor.non_f1_record` | 3,643 | generic/x-module/native record ctors |
| `method.recv.field_nonf1` | 2,191 | method receiver = field of a non-F1 record |

The **user-generic** slice of this landed as wave-7 (`ctor.non_f1_record`
residue is now essentially `Waker`); the remaining rows are the non-generic
receiver/arg families.

## Out-of-scope / decisions needed

- **Builtin-receiver methods** (`sig.receiver_record` bulk, 31k): bodyless
  (dispatch to runtime symbols, no `gen_body`). NOT in scope for `gen_body`
  deletion -- confirm they are excluded from the "gate excludes nothing"
  completion criterion, else B is unreachable by construction.
- **Overload sets** (`sig.overload_set` 10,911 / 44 shapes): a routing exclusion
  (the shared-impl-hijack gate). Needs a decision: per-stub lowering, or accept
  as a permanent exclusion.
- **str/bytes methods**: real emit (in scope) -- need the receiver-family
  dispatch (str's receiver is not a nominal record). ~2k tail + machinery.

## Recommended sequencing to B

Resumable frames (C: generator/async) are **deferred to last** (decision
2026-07-06). They are independent of the type-family grid (A), the expression
arms (B), and the generics rock (D) -- a generator/async body is rejected at the
function gate, so nothing in A/B/D waits on it, and it waits on nothing in them.
Deferring maximizes visible throughput first and lets the surface clear around
the hardest subsystem. **Tradeoff, stated:** C remains a hard prerequisite for
final deletion (B cannot complete until it lands), so this defers the biggest
timeline *risk* to the end -- we won't confirm the hardest port is tractable
until late. Accepted for now; revisit if A/B/D land faster than expected.

1. **Fan out the type-family grid (A) + its expression residue (B) across
   worktrees now** -- see the fan-out plan below. Biggest mass (~93k leverage),
   construct-disjoint, the primary parallel lever.
2. **Main thread: the non-F1-record / generics frontier (D)** -- the biggest
   serial unlock; also the gate on ctor-MIL deletion.
3. **Mop up the self-contained expression arms (B)** -- if-expr, global reads,
   fstring conv/spec, tuple/container value-position -- parallel cells.
4. **Settle the out-of-scope decisions** so "the gate excludes nothing" is
   well-defined and B is provably reachable.
5. **Resumable frames (C) last** -- generator + async, then for-each iterator
   protocol / closures / remaining match-try-with tiers. The completion floor;
   once it and D land, the dispatchers delete together.

Track progress on the shape meter (distinct shapes routed), not the body count.
Deletion of any AST emit function is a near-end event under per-body routing --
the curve stays < 100% until C and D both land, then the dispatchers retire
together.

## Fan-out plan (waves of construct-disjoint cells)

Each cell is a self-contained frontier developed in its own worktree with its own
`--thir-codegen --no-exec` byte-diff, integrated **sequentially** with a combined
corpus byte-diff after each merge (per the acceleration package). Cells are
organized as **type-family verticals** (a family's param + return + field + arg +
literal arms move together -- cohesive, and cheaper than a position-horizontal
slice that touches every family at one gate). The shared signature gates
(`_f1_param_eligible`, `_eligible_return`, `_ctor_param_eligible`) take a one-line
arm per family; those lines are the only cross-cell textual overlap and resolve
trivially at integration order.

**Wave 1 -- independent, start immediately (each ~a session):**

| Cell | Scope | Primary files | Leverage |
|------|-------|---------------|---------:|
| **W1-container** | list/dict/set/Array/Span in param + return + field-read/write + local + subscript-element (incl. `list[record]`, dict->record); container literals in value position. PARTIAL: span params + `Own[list/dict/set]` returns (bare-name/literal sources) + `Own[str/bytes]` returns landed 2026-07-06; still open: container fields, `Own[container]` params, set membership, `list[T]` generic elements, call/field return sources (field-receiver subscript writes landed: name receivers in the wave-2 setitem cell, F1-record elements incl. field receivers in the wave-4 record-writes cell) | predicates.py, lower/{statements,expressions}.py, emit.py | container fields + subscript-elem remain |
| **W1-tuple** | MOSTLY DONE (value-tuple rung): `THIRTupleLiteral` + value-tuple param/return/arg slots landed; VALUE-capture non-value (record/`Own`) elements land as storage-form decls (`_storage_record_tuple`); remaining: tuple fields, ref-capture elements | predicates.py `_value_tuple` / `_storage_record_tuple`, lower/expressions.py, emit.py | residue only |
| **W1-ifexpr** | DONE (wave 2): `THIRIfExpr` for scalar/Char/enum/str results; non-value/bytes results tagged `ifexpr.result_type` | lower/expressions.py, emit.py, nodes.py | first-rejects 1073 -> 18 |
| **W1-globalread** | DONE for value globals (waves 2-3): seeding + Ptr[T] writes + native/imported spelling (THIRName.cpp via imported_variable_cpp); remaining: non-value pointer-slot indirection, Optional-value globals (AST miscompile, BUGS.md), module-attr dotted reads | lower/expressions.py `name` arm, emit.py | residue only |
| **W1-fstring** | DONE (wave 2): `!r`/`!s` + constant format specs across all mirrored arg types; unmirrored types under conv tagged `fstring.arg_wrap` | lower/expressions.py `_fstring_eligible`, emit.py | landed |

**Wave 2 -- start after the wave-1 gate arms land (they extend the same gates / need a family from W1):**

| Cell | Scope | Depends on | Leverage |
|------|-------|-----------|---------:|
| **W2-union-sig** | union in param + return signature positions (U-series read/write already landed; this is the sig-gate admission) | union emit arms (landed) | part of `sig.param_type`/`sig.return_type` |
| **W2-own-nonrecord** | `Own[str/bytes/container/tuple]` param/return slots (Own[T]-generic + Own[record] done) | W1-container, W1-tuple | part of own:* |
| **W2-optional-nonrecord** | `Optional[non-record]` in param/return/field (Optional[record] done) | -- | modest |
| **W2-binop-coerce** | mixed/widening arith coercion node, Char concat, non-bool logical (temp+ternary) | -- | ~3.9k (`binop.shape` + `cond.bin_op`) |
| **W2-subscript-elem** | non-value subscript element reads (`list[record][i]`, dict->record) | W1-container | 713 |

**Main thread (NOT fanned out -- serial, one owner each):**

- **M-generics** (bucket D): LANDED as wave-7 (2026-07-07, branch
  thir-wave7-generics; see IR_DESIGN.md's Wave-7 landing-log entry):
  instantiation-template calls, the ctor instantiation form (incl. the
  `UninitStorage[T]()` MIL hoist), generic native/template callees, and
  type-param compares. The generics-foundation branch (2026-07-16, see
  IR_DESIGN.md's wave note) then landed the plain-TPy explicit
  `f<T>(args)` spelling and instance-method targs (`method_targs_cpp`);
  still deferred: static/module-marker method targs, the `heap_take`
  own-param-move MIL source, `Waker` (async), and the NON-generic MIL
  source fams the drilldown separated out.
- **M-callee-kind** (part of B): imported / cross-module / `@error_return` /
  generic free callees (`call.callee_kind` 2,087) -- new callee-resolution
  machinery, too entangled with call lowering to parallelize safely.
- **M-strmethods** (part of B): the str/bytes-method receiver-family dispatch
  (str's receiver is not a nominal record -- new machinery, ~1.7k + the family).
- **DEFERRED: C (resumable frames)** -- generator/async, last.

Integration cadence: one combined `--thir-codegen --no-exec` byte-diff after each
cell merges; per-branch `/tpy-review` at each branch wrap (the byte-diff owns
correctness). Re-measure the shape meter after each wave to track the curve.

Drilldown convention (standardized after wave 2): when a cell's target bucket
is a mixed tally, land the `note_detail` sub-classifier PERMANENTLY while the
bucket has mass (the `_marker_reject` pattern -- it runs only on already-
rejected calls, first-reject-wins, and self-documents the residue) and delete
it when the bucket empties; hand-written temporary classifiers reverted before
commit are the exception for one-off questions, not the default.
