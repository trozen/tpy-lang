# THIR Generics Foundation — Scoping Brief

*Starting context for a separate session. Read-only design work is safe to start
now; IMPLEMENTATION must wait for THIR wave 4 to merge to master (see
Coordination). Prepared from master `eacfd955c` (wave 3).*

## How to use this brief

This is a **scoping** brief, not a design. It hands you (a) the accurate
current state, (b) the real residue categories measured on master, (c) the
invariants any design must respect, (d) sibling interactions, (e) open
questions, and (f) the method. **Run `/tpy-add-feature` with this as context**,
do the measure-first step yourself (the tags are lossy — this wave burned
multiple sessions trusting them), then present a design before coding.

## Correcting the myth: generics is PARTIALLY BUILT, not zero

An earlier characterization ("THIR has no generic lowering; 89/175 generics
cases marked") is **wrong** and must not seed the design. What ALREADY routes
through THIR today:

- **Generic *functions* with TYPE-kind params route.** `functions.py:296-308`:
  each `[T]` param/return spells as a `TypeParamRef`, `_is_type_param_slot`
  treats it as a form-neutral `val_or_ref_t<T>` pass-through, the template
  signature stays AST-emitted (skeleton). A method's own type params
  (`def m[U](self, x: U)`) compose with a generic record's `T`. Only
  **INT-kind params** (`[N: int]`) reject → `sig.generic_fn` (no T-slot arm
  for an int read as a value).
- **Generic *record methods* route** — `functions.py:1556-1584`: `self` is
  `Record[T,...]`, each type param a `TypeParamRef`, templated bodies lower.
- **Spelling is byte-identical by construction** — `tpyc/thir/lower/generics.py`
  is a 20-line invariant point: `expand_fi_template` (in `typesys`, shared with
  the AST's `gen_call_from_fi`) is the ONE substitution rule; use-site type
  spelling goes through the same `to_cpp_stored()` / `lc.render_type` the AST
  uses. **Do not introduce a second spelling path** — an arm whose AST spelling
  applies a transform THIR can't mirror stays gated to AST.

So this is **finish a partially-built frontier**, not build from scratch. The
~89 marked cases reject on specific *body cells*, not on the signature.

## The real residue (measured on marked `generics/` cases via the reject probe)

Reject reasons across a sample of marked cases, ranked by frequency:

| Reject | What it is | Notes |
|---|---|---|
| `call.generic_arg_slot` / `call.generic_arg_shape` | passing args INTO a generic function call (the caller `main` bodies) | dominant `main`-body blocker; the T-slot arg-passing arm |
| `expr.method_call` | method call on a T-typed / protocol-bounded value inside a generic body (`take_first`, `get_k`, `unwrap`) | bounded-protocol method dispatch through a TypeParamRef receiver |
| `stmt.var_decl:decl.slot_type` | local decl of a bounded/generic-typed value (`x = factory()` → generic return) | **same arm wave-4 Executor E edits** (statements.py decl gate) |
| `expr.coerce` | coercion in a generic body (`get` bodies) | TypeParamRef/bounded coercion dispositions |
| `stmt.return:return.slot_type` | returning a T-typed / generic value | return-slot arm on a TypeParamRef |
| `subscript.recv.call` | subscript on a call result in a generic return | composed |
| `method.record.tag`, `method.fi_kind` | specific method-dispatch cells (covariant tag, native) | |

**Key insight:** the generics residue is NOT a self-contained new mechanism. It
is the **existing decl / call / method / coerce / return arms, with a
`TypeParamRef` or bounded type flowing through them**. The work is *threading
TypeParamRef + bounds* through arms that already exist for concrete types —
plus a few genuinely-new pieces (below).

### Genuinely-new pieces (not just threading)

- **INT-kind type params** (`[N: int]`, e.g. `Array[T, N]` sizing) — `N` read as
  a value has no T-slot arm. `sig.generic_fn`.
- **Static-protocol params** (`x: Awaitable[T]` → deduced `T_x` template arg +
  concept constraint). The frame/signature is skeleton (`gen_async.py`
  `_ParamStorage.STATIC_PROTOCOL`, `protocols.py protocol_param_template_name`),
  but THIR must lower the leaf reads on a concept-typed value. **This is the
  blocker behind the deferred resumable template-frames** (`res.generic`,
  `res.param_type` static-protocol) — routing it here unblocks that too.
- **Bounded type params** (`[T: Comparable]`, assoc-type bounds) — the
  `assoc_type_*` / `bound_*` cases: bounded-protocol method dispatch + the
  associated-type projection.
- **Explicit type-args on generic calls** (`f[int](x)`) — `explicit_type_arg_*`.

## Invariants any design must respect

1. **One spelling path.** All use-site type spelling goes through the AST's
   functions (`expand_fi_template`, `to_cpp_stored`, `lc.render_type`). Never
   add a parallel spelling.
2. **Template signature/frame stays skeleton.** THIR lowers *leaf renders*
   inside the templated body; the template header, factory, and (for resumable)
   the frame struct are emitted by the existing AST/skeleton path from the
   signature. Do not re-derive them in THIR.
3. **Form-neutral T slots.** A `TypeParamRef` slot takes no borrow/storage lift
   (`val_or_ref_t<T>` resolves value-vs-ref per instantiation) — see
   `functions.py:837`. Bounds and concrete substitutions must preserve this.
4. **Byte-identical to the AST oracle**, verified by the whole-corpus byte-diff
   (the ratchet + overlay). Every routed cell must match `expected/src/*.cpp`.

## Sibling interactions (survey these before designing — Phase 2)

- **generics ↔ protocols.** Static-protocol params and bounded type params are
  the same concept-constraint machinery (`protocols.py`). Design them together;
  a design that does `[T]` but not `[T: Proto]` is the recurring decay pattern.
- **generics ↔ resumable.** `res.generic` / `res.generic_record` (resumable.py
  :364-370) and static-protocol coro params are downstream of this foundation —
  the deferred resumable template-frame work (THIR wave 4) unblocks here.
- **generics ↔ records.** Generic record methods already route
  (functions.py:1556); extend consistently to free generic funcs' residue.
- **generics ↔ the general arms.** Because the residue threads through
  decl/call/method/coerce/return, a change here interacts with every concrete-
  type case in those arms — the byte-diff is the guard.

## Open design questions

- Is the right unit **one arm at a time** (thread TypeParamRef through decl,
  then method-call, then call-arg-slot, then coerce, then return — each a cell),
  or **one concept at a time** (all of `[T: Proto]` bounded dispatch, then
  int-kind, then explicit-targs)? The measured residue suggests **arm-at-a-time
  threading** clears the bulk (call-arg-slot + method-call + decl + return cover
  most `main`/accessor bodies), with the concept pieces (int-kind, static-proto,
  bounds) as separate cells.
- Do bounded-protocol method dispatch and static-protocol params share enough to
  build once? (Likely yes — both are concept-constrained receivers.)
- INT-kind params: is a value T-slot arm bounded, or does it pull in
  `Array[T, N]` sizing broadly?

## Method (do not skip — this wave's hard-won lesson)

1. **Measure first.** Run the reject probe over ALL marked `generics/` cases
   (harness: `scripts/thir_migration/thir_scan.py <case>...`; it sets `thir_codegen=True`,
   wraps the lower fns, prints per-body reject reasons). Build the real
   per-arm / per-concept residue table. The tags are LOSSY — verify each
   category against the actual body `.py` + `expected/src/*.cpp` oracle before
   costing it. (Three ARCH picks collapsed this wave from trusting tags.)
2. **Pick the arm/concept with the highest verified clean-flip yield**, not the
   biggest tag. Expect whole-CASE flips to need SEVERAL arms cleared together
   (generics cases are multi-reject: `main` on call-arg-slot + accessor on
   method-call + `get` on coerce) — so plan to route a *cluster* of arms before
   any case flips.
3. `/tpy-add-feature`, present the design (arm-threading plan + the invariant),
   get approval, implement per-arm with commit-per-cell, flip via
   `--thir-check-flip`, keep the byte-diff green.

## Coordination with THIR wave 4 (IMPORTANT)

- **The generics residue lives in the SAME lowering arms wave 4 is editing** —
  `statements.py` decl gate (Executor E), `expressions.py` method/call arm
  (Executor C2), `checks.py`, `predicates.py`. It is **NOT territory-disjoint**.
- Therefore: **DESIGN now (read-only, zero conflict); IMPLEMENT only after wave
  4 merges to master.** Branch the implementation off the post-wave-4 master so
  you build on the routed decl/method/call arms rather than fighting them.
- Wave 4 is ~1–2.5 hr from merge-ready as of this brief. Design fills that gap
  productively; implementation starts on a clean base.

## Pointers

- `tpyc/thir/lower/functions.py` — generic-fn signature routing (296), the
  `_is_type_param_slot` / TypeParamRef form-neutral arms (670, 837), generic
  record-method self-feed (1556-1584).
- `tpyc/thir/lower/generics.py` — the spelling invariant + `expand_fi_template`.
- `tpyc/typesys.py` — `expand_fi_template` (the shared substitution rule).
- `tpyc/codegen_cpp/protocols.py` — `protocol_param_template_name`,
  `fn_param_template_name` (the concept-constraint skeleton the leaves lower
  inside).
- `docs/IR_DESIGN.md` — THIR design + the per-case operating model + wave
  orchestration lessons. `docs/THIR_COMPLETION_LEDGER.md` — per-construct
  porting reference (M7 = template frames).
- Reject probe: `scripts/thir_migration/thir_scan.py`.
