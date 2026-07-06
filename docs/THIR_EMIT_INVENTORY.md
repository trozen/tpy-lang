# THIR Emit-Arm Inventory

The map for completing the THIR migration (deleting the AST codegen path).
Drives the work by the **finite emit surface we must port** (~236 emit functions
/ ~380 dispatch arms / ~36k lines in `tpyc/codegen_cpp/`), not by the
combinatorial ~5,100 body *shapes* those arms generate. Shapes are the products;
arms are the generators. Chasing shapes is asymptotic; porting arms is bounded.

Companion to `THIR_COMPLETION_LEDGER.md` (deletion-target model + landing log)
and the shape meter (`tpyc/thir/shape.py`, `$THIR_SHAPES_JSON`) which measures
progress. Leverage figures below are real-corpus blocked-body counts from a full
`--thir-codegen` run (2026-07-06, master @ edc226168e; 691/5136 distinct shapes
routed).

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
its lone caller only goes dead when EVERY record's ctor routes -- gated on the
union of generics (`ctor.non_f1_record`), all param form-rungs
(`ctor.param_type`), all field form-rungs F3+ (`ctor.mil_field`), AND full
body-statement coverage. It lands with the rest, not before.

## The work in four buckets

Ranked by leverage; P = parallelizable (disjoint type-family cells, worktree
fan-out), S = serial (shared machinery / design-heavy, one main thread).

### A. Signature type-family grid -- the biggest mass [P]

Gate-widening in `_f1_param_eligible` / `_eligible_return` / `_ctor_param_eligible`
to admit more type-families in param/return/ctor-param position. Disjoint per
type-family -> the primary parallel-worktree lever.

| Gate | leverage | distinct shapes | missing type-families |
|------|---------:|----------------:|-----------------------|
| `sig.param_type` | 50,937 | 631 | str, bytes, container, tuple, union, Own[non-record], protocol (scalar/record/opt done) |
| `sig.return_type` | 34,853 | 402 | record, ptr, container, tuple, union, Own[T]/generic (scalar/str/opt/borrow-tuple done) |
| `ctor.param_type` | 7,094 | 77 | same as param, at ctor params |

### B. Expression arms feeding var-decl / return / if / expr-stmt [mostly P]

Most statement "leverage" is really the expression init/condition residue
surfacing at the statement (var-decl/return/expr-stmt/if structures are already
ported). The arms, by `gen_expr`/`gen_method_call` dispatch:

| Arm | AST emit | status | biggest gaps | leverage | P/S |
|-----|----------|--------|--------------|---------:|-----|
| `TpyMethodCall` | expressions.py:3441 | partial | `method.marker` (static/super/module-qual) 8957; **str/bytes methods** (receiver not a nominal record -- new receiver-family dispatch) ~1.7k; non-F1/method receivers 4.5k; multi-overload; non-value returns | ~17k | P (grid) + S (str-family machinery, non-F1 receivers) |
| `TpyCall` | expressions.py:2874 | partial | non-scalar returns `call.ret_type` 2433 (grid-fill, return-slot); imported/cross-module/error_return/generic callees `call.callee_kind` 2087 (new machinery); `call.special_form` 1084 (bespoke cast/isinstance/enum/macro arms) | ~7k | mixed |
| `TpyBinOp`/`ChainedCompare` | expressions.py:1883 | partial | mixed/widening arith (coercion node), Char concat, record operator-dunder indirection, non-bool logical (temp+ternary machinery) | ~3.9k | P (grid) + S (non-bool logical) |
| `TpyFString` | expressions.py:6422 | partial | `!r`/`!s` conversions + format-spec fields (new per-arg render) | 1,892 | P |
| `TpyName` global read | expressions.py:1432 | partial | module/native/imported/cross-module global reads (qualified-symbol resolution + non-value global indirection) | 1,113 | P (self-contained) |
| `TpySubscript` | expressions.py:6094 | partial | non-value element reads (`list[record]`, dict->record/container), optional-runtime-check, narrowed/non-name receivers | 713 | P |
| `TpyFieldAccess` | expressions.py:4319 | partial | non-value field reads (record/container/opt/union field), non-F1 receiver | ~1k | P |
| `TpyIfExpr` (ternary) | expressions.py:6689 | **none** | whole arm unlowered | 747 | P |
| list/dict/set literal (value position) | expressions.py:4510/4781/4826 | partial | only decl-init + print-arg today; value/return/call-arg positions + non-scalar elements missing | ~700 | P |
| `TpyTupleLiteral` | expressions.py:5693 | **none** | whole arm unlowered (tuple family) | tuple | P |
| `TpyListRepeat` `[0]*n` / walrus / lambda / genexpr | 4857/6514/6816/5170 | **none** | unlowered; walrus/lambda/list-repeat low-frequency, genexpr = serial | low | walrus/lambda S |

Fully ported expr arms (no work): int/float/bool/str/bytes literals, `len()`,
scalar ctors, slice ctors, enum lookup, the arg-temp/move/union-lift/optional-ptr
call-arg machinery.

### C. Serial control-flow subsystems -- the completion floor for B [S]

These can't be parallelized and set the timeline. Front-load their design.

| Subsystem | status | machinery still needed | leverage |
|-----------|--------|------------------------|---------:|
| **generator + async** (`yield`/`await`) | none | the resumable-frame lowering ported into THIR (state-machine struct + `poll`/`__next__`, resumable CFG). Every `async def`/generator body rejected at the function gate today | ~9,600 (`sig.async` 7739 + `sig.generator` 1876) -- **largest serial rock** |
| `TpyForEach` | partial | non-native-iterable `__iter__`/`__next__` protocol loop; async-for (shares resumable frame); for-else `goto`; enum/consuming/hoist-loop-var; record/str tuple targets | 2,005 |
| comprehensions (list/dict/set partial, genexpr none) | partial | value-position beyond decl-init/print-arg sinks; genexpr | moderate |
| `TpyNestedDef` (closures) | none | closure/nested-fn lowering end-to-end | 12 (rare) |
| `TpyWith` | partial | async-with (resumable); non-F1/temp-registering managers | ~50 |
| `TpyTry` | partial | return-tier (`@error_return`/`ReturnException` through the finally chain); in-branch/in-loop fresh predecls | ~41 |
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

The **user-generic** slice of this (not builtins) unblocks with the generics
frontier; it is the single highest-leverage serial main-thread target after the
signature grid.

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
| **W1-container** | list/dict/set/Array/Span in param + return + field-read/write + local + subscript-element (incl. `list[record]`, dict->record); container literals in value position | predicates.py, lower/{statements,expressions}.py, emit.py | high (part of param/return + subscript 713) |
| **W1-tuple** | `TpyTupleLiteral` (none today) + tuple in param/return/field/arg + tuple value-position | predicates.py `_f1_tuple`, lower/expressions.py, emit.py | tuple family (part of param/return) |
| **W1-ifexpr** | `TpyIfExpr` ternary -- whole arm, currently `none` | lower/expressions.py, emit.py, nodes.py (new node) | 747 |
| **W1-globalread** | `TpyName` module/native/imported/cross-module global reads (qualified-symbol resolution + non-value global indirection) | lower/expressions.py `name` arm, emit.py | 1,113 |
| **W1-fstring** | fstring `!r`/`!s` conversions + format-spec fields | lower/expressions.py `_fstring_eligible`, emit.py | 1,892 |

**Wave 2 -- start after the wave-1 gate arms land (they extend the same gates / need a family from W1):**

| Cell | Scope | Depends on | Leverage |
|------|-------|-----------|---------:|
| **W2-union-sig** | union in param + return signature positions (U-series read/write already landed; this is the sig-gate admission) | union emit arms (landed) | part of `sig.param_type`/`sig.return_type` |
| **W2-own-nonrecord** | `Own[str/bytes/container/tuple]` param/return slots (Own[T]-generic + Own[record] done) | W1-container, W1-tuple | part of own:* |
| **W2-optional-nonrecord** | `Optional[non-record]` in param/return/field (Optional[record] done) | -- | modest |
| **W2-binop-coerce** | mixed/widening arith coercion node, Char concat, non-bool logical (temp+ternary) | -- | ~3.9k (`binop.shape` + `cond.bin_op`) |
| **W2-subscript-elem** | non-value subscript element reads (`list[record][i]`, dict->record) | W1-container | 713 |

**Main thread (NOT fanned out -- serial, one owner each):**

- **M-generics** (bucket D): the non-F1-record / user-generic frontier. Highest
  serial leverage; gates ctor-MIL deletion. Design pass first (`/tpy-add-feature`).
- **M-callee-kind** (part of B): imported / cross-module / `@error_return` /
  generic free callees (`call.callee_kind` 2,087) -- new callee-resolution
  machinery, too entangled with call lowering to parallelize safely.
- **M-strmethods** (part of B): the str/bytes-method receiver-family dispatch
  (str's receiver is not a nominal record -- new machinery, ~1.7k + the family).
- **DEFERRED: C (resumable frames)** -- generator/async, last.

Integration cadence: one combined `--thir-codegen --no-exec` byte-diff after each
cell merges; per-branch `/tpy-review` at each branch wrap (the byte-diff owns
correctness). Re-measure the shape meter after each wave to track the curve.
