# Intermediate Representations -- Design

## Status

| Feature | Status |
|---------|--------|
| THIR node definitions (`tpyc/thir/nodes.py`) | Increments 1-5 -- value-scalar slice (+ range-for, double float, bool, comparison-as-value) |
| AST + sema -> THIR lowering (`tpyc/thir/lower.py`) | Increments 1-5 -- value-scalar slice (+ range-for, double float, bool, comparison-as-value w/ resolved-local-type tracking) |
| `--dump-thir` debug output | Done (increment 1) |
| THIR-backed codegen context | Increment 1 -- flag-gated (`tpyc/thir/emit.py`) |
| Codegen migration from analyzer/AST to THIR | Increments 1-5 -- value-scalar bodies incl. if/elif/else, while, range-for, double float, bool, comparison-as-value |
| THIR form fact (Open Q 9/11/12) | **Rungs F1-F2 (2026-06) through increment 55 landed** -- the per-increment history lives in the Landing Log section below (moved out of this table cell: a single-line cell was a recurring merge hazard for parallel branches) |
| MIR node definitions (`tpyc/mir/nodes.py`) | Not started |
| THIR -> MIR lowering (`tpyc/mir/lower.py`) | Not started |
| `--dump-mir` debug output | Not started |
| MIR liveness pass | Not started |
| MIR move/copy lowering and move optimization | Not started |
| MIR advisory loan checker (default mode) | Not started |
| MIR safe opt-in enforcement mode | Not started |
| MIR-backed codegen | Not started |
| Retirement of old sema/codegen ownership logic | Not started |

A throwaway Phase-1 spike (2026-06) validated the THIR boundary -- byte-identical
codegen from THIR with no analyzer reference, on an arithmetic slice; see Rollout
Plan -> "Phase-1 spike validation".

**Completion tracking:** `THIR_COMPLETION_LEDGER.md` is the deletion roadmap --
which AST body/form codegen component each rung is working toward deleting, what
gates each deletion, and the registry of deferred cells. This doc is the design +
landing log; the ledger is what is *left*. Sequence against the ledger, not against
routing %.

## Landing log

One bullet per landed increment (moved verbatim out of the Status
table's form-fact cell). Append new entries at the END as list
items -- one increment per bullet keeps parallel-branch merges
conflict-free.

- **Rungs F1-F2 landed (2026-06)** -- form facts on the IR (`Form` tag, `THIRFormConvert`, `THIRFieldAccess`). F1: single-assignment non-value record locals (`T&` alias / `optional_to_ptr`) + scalar field reads. F2: reseatable `T*` pointer-locals (lvalue reseat + the rvalue `__slot_N` rebind machinery, `->` reads) and the Optional borrow<->storage write/return (`ptr_to_optional` copy / `ptr_to_optional_move` move) + `None` (`std::nullopt`). Byte-identical with `--thir-codegen` forced over the full corpus.
- **Method frontier M1 landed (2026-06)** -- a callable-kind axis orthogonal to the form ladder: plain instance methods route (`self` -> `this` via `THIRSelf`, readonly/const self, scalar params), so F1/F2 finally route real corpus code.
- **Scalar field writes landed (increment 10)** -- `recv.field = <scalar>` off any F1-record receiver (plain `THIRAssign`, no form lift), more than doubling corpus routing.
- **Scalar augmented assignment landed (increment 11)** -- `x += y` / `recv.field += y` lowers to `target = (target OP value)` via `THIRBinOp` (+ a `paren_wrap` flag for the AST aug-assign's no-paren RHS).
- **Method frontier M2 landed (increment 12)** -- F1-record params on instance methods (const verdict via the method's own `const_borrow_params` on the owning record; no readonly carve-out -- forced-const and inferred-const coincide for plain ref params), routing 2835 -> 3163 bodies / 534 -> 576 cases.
- **Readonly free functions landed (increment 13)** -- removed the `is_readonly and not is_instance_method` exclusion (forced/inferred const coincide for free functions too), 3163 -> 3165 bodies / 576 -> 577 cases.
- **Ctor frontier M3a landed (increment 14)** -- pure-MIL scalar constructors of flat records: a whole-ctor `THIRConstructor` (member-init-list split from body) + a dedicated MIL-tail emitter, the signature staying on the AST path; 3165 -> 4652 bodies / 577 -> 1160 cases (+1487 ctors).
- **Ctor M3b-copy landed (increment 15)** -- record / `Optional[record]` MIL fields, copy arm (the `ptr_to_optional` cell + `None` -> `std::nullopt` + non-own record-param copy), reusing F2b/F2c in MIL position with no new emit; 4652 -> 4691 bodies / 1160 -> 1169 cases (+39).
- **Ctor M3b-move landed (increment 16)** -- own-param `std::move` MIL sources (the common `Own[T]` ownership ctor; THIR's first `std::move` emit, a `move` flag on `THIRMilInit`); 4691 -> 4703 bodies / 1169 -> 1172 cases (+12).
- **Ctor M3b-rvalue landed (increment 17)** -- record *value* sources (`_is_record_value_source`: F1-record param name / ctor-call rvalue / param field-read), which construct a record or Optional field directly (`ptr_to_optional` reserved for borrow-`T*`), plus own-optional params (`Own[Inner|None]` / `Optional[Own[Inner]]`) and the `copy()`-on-Optional source; 4703 -> 4739 bodies / 1172 -> 1182 cases (+36).
- **Ctor M3c-trivia landed (increment 18)** -- the first non-empty ctor body: docstring / `pass` non-init statements (`THIRNoOpStmt`, no code; `pass` keeps its loc for the `// pass` comment, a docstring lowers loc=None matching the AST's no-comment), chain intact so every field init still hoists; 4739 -> 5030 bodies / 1182 -> 1210 cases (+291).
- **Ctor M3c-demotion landed (increment 19)** -- the hoist/demotion split: a field init that can't hoist (or follows a `chain_broken` non-init statement) demotes into the body, lowered via the shared `_body_eligible`/`_lower_stmt` machinery; only the `chain_broken` cascade needed explicit reproduction (the other demote triggers are subsumed by the eligibility gate); 5030 -> 5031 bodies / 1210 -> 1211 cases (+1, the demotion tail is <1% of corpus ctors -- validated by hand-written units).
- **Ctor M3d-1 landed (increment 20)** -- a single same-module F1 base: `super().__init__(args)` -> a structured `THIRBaseInit` prepended to the field MIL (the placeholder `base_inits: tuple[str]` becoming `tuple[THIRBaseInit]`); 5031 -> 5057 bodies / 1211 -> 1216 cases (+26).
- **Ctor M3d-2 landed (increment 21)** -- multi-base (parent-order-sorted base inits + the `BaseN.__init__` form) + inherited-field writes (body branch + `body_written_self_fields` + `expr_reads_self_field`), completing the ctor frontier (M3a-M3d); 5057 -> 5071 bodies (+14). Remaining ctor cells are cross-axis-blocked: demoted record-field writes; non-trivia body statements outside the statement-shape slice; F3+ field forms; native/generic records; call-arg / call+`copy()`-write sources.
- **F3 tuple form opened (increments 22-24)** -- the storage->borrow read (`tuple_to_pointer`: borrow-form tuple return + storage-tuple `auto&&` alias locals) + borrow->storage write (`tuple_to_storage`, tuple-field write off a borrow tuple param) for pointer-repr tuples of scalar / F1-record / `Optional[F1-record]` elements; 5071 -> 5081 bodies. Still AST-side: reassign (BORROW_TUPLE), the `tuple_to_storage_move` move arm, subscript/loop-var/unpack sources, tuple-literal MIL. F4-F-final not started.
- **Statement-shape axis opened (increment 25)** -- value-result tuple subscript reads (`t[N]` -> `std::get<N>(t)`, a new `THIRSubscript` node; value-scalar tuple params admitted as `const std::tuple<...>&`); 5081 -> 5085 bodies.
- **Record-element reads (increment 26)** -- `t[N].field` off a plain-record element (`std::get<N>(t)->field` / `.field` per the element-form `_tuple_subscript_yields_borrow_ptr` mirror); 5085 -> 5087 bodies.
- **Optional-element member access (increment 27)** -- `t[N].field` on an unproven `Optional[record]` -> `deref_check(<T*>).field` (a `deref_check` flag on `THIRFieldAccess`, storage source lifted via `optional_to_ptr`); completes the tuple-READ frontier; 5087 -> 5089 bodies.
- **Write position (increment 28)** -- `t[N].field = / += <scalar>` off a record element -> `std::get<N>(t)->field = ...` (position-neutral `_field_over_subscript_ok` added to the scalar-field-write + aug-assign gates); completes the tuple-subscript FAMILY; 5089 -> 5092 bodies.
- **Container subscript reads landed (increment 29)** -- `c[i]` off a `list[scalar]` / `dict[fixed-int, scalar]` param -> `::tpy::__getitem__(c, i)`, generalizing `THIRSubscript` to its canonical shape (`index: THIRExpr` + `bounds_safe`, emit dispatched tuple-vs-container on receiver type); with the master-merge-rebased baseline, 5111 bodies / 1229 cases total.
- **Container iteration landed (increment 30)** -- `for x in <NativeIterable>:` over a value-scalar element (a new `THIRForEach` begin/end loop) + the `len(c)` builtin (`::tpy::__len__`, a `native_name` on `THIRCall`); `range(len(c))` now routes and lights up the bounds-safe subscript branch. Routing 5128 bodies / 1237 cases -- a small gain despite the big loop surface, because loop bodies overwhelmingly use `print` / `.append` (in 371 of 393 for-loop files), so the routing bottleneck is now those body constructs, not the statement shapes.
- **Record-element loops landed (increment 31)** -- `for x in <list[record]>:` with a borrow-alias loop var (`auto&&` / `const auto&`, `.field` access like a record param), threading sema's `const_loop_var` through `THIRForEach` + a `_container_record_iter` param predicate; 5128 -> 5176 bodies / 1237 -> 1241 cases (+48, admitting the `list[record]` param unblocks whole record-iterating functions, a larger gain than the scalar shape cells).
- **Expression statements landed (increment 32)** -- `print(<scalar/str-literal args>)` (a `THIRPrint` mirroring `gen_print`'s stream chain) + bare same-module free-function call statements (`THIRExprStmt`); the first expression statements THIR routes (the shape was previously 0% covered). 5176 -> 5629 bodies / 1241 -> 1334 cases (+453 -- the biggest single-cell gain, since print gated a huge tail of small functions). Fixed two pre-existing gaps print unmasked: the native/export-linkage call qualification (`::name`) and the tuple-unpack shared-source-comment dedup (`no_source_comment`).
- **Method-call sites landed (increment 33)** -- a new `THIRMethodCall` mirrors `_gen_method_call`'s pass-through subset across all three `gen_call_from_fi` emit arms (`@cpp_template` `xs.sort()`, `@native` free function `xs.pop()` -> `::tpy::pop_back(xs)`, plain/renamed member `xs.append(v)` -> `xs.push_back(v)`) on bare-name `list[scalar]` / `dict[fixed-int, scalar]` receivers with value-scalar args, statement and value position; 5629 -> 5640 bodies / 1334 -> 1340 cases. The small gain exposed the co-blocker: most `.append` sites sit on container-*literal locals*, whose var-decls don't route.
- **Container-literal locals landed (increment 34)** -- `THIRContainerLiteral` routes list/Array/dict/set literal decls (vector-vs-array per sema's resolution), the local enters `declared` so the param-receiver shapes light up on locals; fixed the PendingListType/IntLiteralType leakage at use sites; rejected reassigned (aliasing pointer-local) literals and the no-paren literal-operand binop branch; 5640 -> 5678 bodies / 1340 -> 1354 cases.
- **Container call args landed (increment 35)** -- bare-name container args into non-Own concrete container params pass through (`use_list(xs)`; Own/Span/protocol slots stay AST); 5678 -> 5689 bodies / 1354 -> 1356 cases.
- **Logical and/or/not + inline chained compares landed (increment 36)** -- bool-result `&&`/`||` (bare-operator `THIRBinOp`), `THIRUnaryNot`, and the inline chained-compare left-fold; 5689 -> 8124 bodies solo, the largest single-cell gain (conditions gate everything).
- **Scalar type-constructor calls landed (increment 37)** -- `Int32(0)` / `UInt32(x)` / `Float64(1.5)` / `bool(n)` route via the sema-substituted `__init__` `cpp_template` carried on `THIRCall` (receiver-less expansion); closes the `Int32(0)` ctor-init co-blocker; 5689 -> 5783 bodies solo. **F6 str slice opened: str values (S1 cell, increment 38)** -- str/StrView params, literal-init locals (the PendingStrType view/owned resolution is sema-final pre-lowering, read through ViewVarInfo like `_resolve_view_storage`), print args (raw stream), comparisons (resolved dunder templates), `len(s)`, owned-str returns and same-type call args; the view->owned copy at a decl-init/return sink is an explicit `THIRFormConvert` (BORROW str source -> STORAGE, `std::string(x)`, the `_view_source_to_owned` chokepoint) driven by the load-bearing str name-form tag (string_view param / view local = BORROW, owned local = STORAGE, literal = VALUE and never wrapped). Rejected (AST path): str aug-assign/concat (S3), cross-type str-family coercions (str<->StrView<->String, position-dependent), reassigned str params (owned-copy prologue), multi-overload callees with str-literal args (the `_wants_str_literal_pin` view pin), `String`/`Char`/`Literal[str]` bindings, f-strings (S2), bytes (S6 -- the family-generic plumbing is in place). Next co-blocker: f-string print args (S2). Increments 36-38 were parallel cells integrated sequentially; the INTEGRATED tally on the merged tree is **22165 bodies / 3271 cases** (each increment's own numbers are its solo run).
- **F6 S2 f-strings landed (increment 39)** -- `THIRFString` mirrors `_gen_fstring` (all-literal `std::string` incl. the NUL explicit-length arm, `std::format` / NUL `vformat`, brace escaping) with per-arg wrap templates decided at lowering; the owned STORAGE result feeds every S1 sink; +27 bodies solo.
- **F6 S3 concat/`+=` landed (increment 40)** -- same-type `a + b` via the resolved `__add__` template (owned `String` result), `String` locals, and the str `+=` / `x = x + y` self-append peephole as a dedicated `THIRStrAppend`; +21 bodies solo.
- **F6 S4 subscript/slice/iteration landed (increment 41)** -- `s[i]` -> Char (checked dunder / bounds-safe operator[]), non-stepped `s[a:b]` -> `THIRStrSlice` (a BORROW string_view, view sinks only), `for c in s` Char loop vars, Char params/returns/prints/call-args, `THIRCharLiteral` compare operands, and the reassigned-StrView-param widening (`param_needs_copy_for_reassign` keys the reject); +331 bodies solo. Increments 39-41 were parallel cells integrated sequentially; the INTEGRATED tally is **22545 bodies / 3271 cases**.
- **F6 cross-type str-family coercions landed (increment 42)** -- the sema str<->StrView<->String TpyCoerce arms, position-disposed at lowering (`_coerce_disposition`, mirroring the coercions.py codegen lambdas off the node's own facts: coercion name, `context_kind`, `expected_type` Own-ness, literal-ness of the inner): identity positions extend the `THIRCoerce` passthrough (a view-target coerce sets BORROW itself -- its value is a view whatever the source's form), materializing positions (`std::string(x)`: `strview_to_str` at INIT/ASSIGN/RETURN, `str_to_string` at non-literal ARG, `strview_to_string` everywhere) lower to the S1 view->owned `THIRFormConvert`, the family respelling riding `result_type` so the emit stays one chokepoint. Alongside (no coercion involved -- a concat result renders bare): `String` compare operands, `String` f-string args, and non-Own `String` call slots; the multi-overload str-literal pin guard now coerce-peels like the AST's gen_call_arg. Rejected: `Own[...]` ARG slots (the gen_call_arg cascade frontier), the `Optional[str/StrView]` per-element arms (statement-expression hoist), `char_to_*` (own wrap renders). Slices now flow into owned sinks (`return s[1:3]`, `t: str = s[1:3]`), closing the S4-in-S1 composition gap; 22642 -> 22974 bodies / 3280 cases (+332 solo).
- **F6 S5 dict[str]/container-of-str landed (increment 43)** -- owned-`str` container elements/keys/values across the already-routed container shapes: `dict[str, scalar|str]` / `list[str]` / `Array[str, N]` params + literal-init locals (and `set[str]` literals), str-keyed subscript reads (the key renders bare -- the `view_key_target` static-storage literal pin fires only for VIEW-typed keys, which the gate excludes), method calls with str args into non-Own str-family slots (`d.pop(k)`; builtin-container methods never take the `_wants_str_literal_pin` path) and owned-str results (`xs.pop()`, STORAGE), str loop vars (a fresh view var usage-resolved to `std::string_view` or an owned `std::string` copy, spelled by the shared `loop_var_binding`), and str-element container literals with the per-slot view->owned wrap (the S1 `THIRFormConvert` reused at element slots -> `std::string(x)`; literals and owned/String/rvalue sources land bare -- the `make_vector`/`make_ordered_*` move arm cannot fire, str is a value type and never in `movable_locals`). A str element/value subscript read carries its resolved form (BORROW when its view var resolved StrView, STORAGE when owned), so the S1 owned-sink copy fires exactly where the AST's `_is_str_view_source` does. Deferred: StrView/BytesView-keyed/-element containers (the actual static-storage-pin shapes), bytes (S6), `Own[str]` method-arg slots (`xs.append(s)` -- the owned-copy wrap / copy-into-temp + `std::move(__tmp_N)` shapes), container subscript writes (`__setitem__`, not routed for ANY family -- the parked cell); 22642 -> 22660 bodies / 3280 cases solo on this branch's baseline (the corpus uses these shapes sparsely; most str-container corpus code also needs the still-deferred shapes).
- **F6 S6 bytes values landed (increment 44)** -- the bytes twin of S1: bytes/BytesView params, literal-init locals (PendingBytesType through ViewVarInfo), owned-bytes returns, comparisons, `len(b)`, same-type call args, `BytesPrinter` print args (`PrintForm.BYTES`). Two shapes with no str analog: a bytes literal's TARGET-dependent render (`THIRBytesLiteral` carries an `owned` flag decided per sink at lowering -- owned `bytes_literal_owned` / empty vector at target-less positions, static-storage span `bytes_literal` at view sinks: view decl-init/reassign and the gen_call_arg span pin into bytes/BytesView slots, coerce-peeled like the AST); and bytes `==` resolving to a @native free-function dunder (no cpp_template) -> a native binop emit arm (`::tpy::bytes_eq(l, r)`, gen_call_from_fi's native shape). The formerly-UNREACHABLE bytes arm of `_emit_form_convert` (`::tpy::bytes_copy`) is now exercised and byte-diff-validated at both owned sinks. Deferred: bytes aug-assign/concat (`bytes_concat`), subscript/slices/iteration, cross-type bytes coercions, bytearray; the mixed owned/view reassign/compare shapes route byte-identically but are a pre-existing AST miscompile (BUGS.md). Routing 22642 -> **27135 bodies / 3280 cases** solo (bytes shapes pervade the stdlib bindings).
- **F6 S4 leftovers + bare numeric-literal call args landed (increment 45)** -- stepped slices `s[a:b:c]` -> `::tpy::str_stepped_slice` over `::tpy::Slice{lo, hi, step}` (an OWNED `std::string` STORAGE result, bare at every sink; `THIRStrSlice` grew `stepped`/`step`), slice-typed variable indices `s[sl]` (`basic_slice`/`slice` params admitted; ctor locals deferred -- an `Int32 | None` Optional-slot ctor), Char-targeted literal decls (`c: Char = 'x'` -> `char c = 'x';`) and Char-slot literal call args (call args now lower against their param slots), non-name slice receivers/iterables (str-family fields off F1-record receivers slice AND iterate; owned-str call results slice; call-result iterables deferred -- an rvalue `auto` capture), bare FLOAT-literal call args into double slots (free calls + record-ctor rvalue sources; `_is_record_rvalue_source` also gained the missing fi/arity gate), and negated INT literals folding to plain literals at lowering (mirroring `_gen_unaryop`'s literal-negation branch -- every admitted literal position lights up at once: args, decl inits, compare operands, subscript indices, slice bounds, `range(-3, 3)`). Deferred: negated float literals / `-x` over names (the `__neg__` template render), negations outside the int32 literal range, slice-object ctor locals, Char record fields, non-name char-subscript receivers; 22642 -> 22716 bodies / 3280 cases (+74 solo). Increments 42-45 were parallel cells integrated sequentially; the INTEGRATED tally on the merged tree is **27561 bodies / 3280 cases** (+4919 over the 22642 base -- the cells' solo gains plus cross-cell composition).
- **F4 unions opened: U1 value unions + the structural form validator (increment 49)** -- value-form unions of eligible scalar members (`Int32 | Float64 [| None]` -> `std::variant<...>`, no borrow/storage duality): params, locals (decl + reassign), returns, same-type compares (variant's own comparison operators, the rb=None bare-operator arm), and same-union bare-name call args (a member-valued arg hoists a `__tmp_N` variant temp on the AST path -- the gen_call_arg cascade frontier); a `None` source renders `std::monostate{}` (target-typed at lowering, like the Char decl). A `_union_binding_divergent` guard on name reads keeps assign-narrowed union reads on the AST path: the bare-variant render at a member-typed sink is a pre-existing AST miscompile (BUGS.md, alongside its union-vs-member compare sibling -- both toolchain-caught, so no green corpus case exercises them). Alongside, the structural form validator (`tpyc/thir/validate.py`, run inside `lower_function`/`lower_constructor`, TODO's second-gate entry): `THIRFormConvert` must convert (form or family-internal type change), `THIRCoerce` must carry its inner form (minus the documented view-target BORROW disposition) -- the F6-review `THIRCoerce` defect class now fails loudly at lowering time; clean over all routed bodies. Sink-position checks + the `THIRBytesLiteral.owned` fold land with U2. Routing unchanged (**27561 bodies / 3280 cases** -- corpus union code narrows / prints / matches, all still gated; the F2 precedent: units are the per-rung net until U3 narrowing lands).
- **Static / property / dunder-operator methods landed (increment 48)** -- the remaining self-contained callable-kind cell: every method kind funnels its body through `gen_body`, so the differences are signature-only (the `static` prefix, the setter's `set_` rename, the getter's ref-return arm) and the cell only widens the eligibility gate. Static methods lower like free functions (no receiver; `record_name` kept so `_param_is_const` reads the method's FunctionInfo off the owning record -- the `_get_method_mutated_params` mirror); property getters/setters lower like instance methods, with the getter+setter pair (one shared method name, a two-entry registry list) carved out of the shared-impl overload-hijack gate since each has its own body; pointer-repr Optional/union getter returns (the `in_property_getter` return-the-field-storage arm) are already rejected by the return gate. Dunder-operator methods were already admitted as plain instance methods since M1 (the C++ `operator==` friend wrappers delegating to them are structural emission, not bodies) -- now pinned by units, with two new explicit rejects: inplace dunders (CONST_PARAMS_METHODS forces const params, a verdict `_param_is_const` does not mirror; their `return self` -> `return *this;` is outside the slice anyway) and `@readonly` staticmethods (emitted with the readonly verdicts dropped); `@total_ordering`-synthesized comparison bodies stay AST (record compare operands, the pinned gen_expr_deref divergence); 27561 -> 28767 bodies / 3280 cases (+1206 solo).
- **Bytes tail landed (increment 46)** -- the S4/S3 twins for bytes: subscript `b[i]` -> UInt8 via the @native `::tpy::bytes_getitem` dunder (name receivers, like the str twin); slices reusing `THIRStrSlice` UNCHANGED (the node carries the resolved @cpp_template, so `bytes_slice` / `bytes_stepped_slice` ride the same emit -- non-stepped -> span BORROW, stepped / slice-var -> owned vector STORAGE; an owned DECL sink takes the S6 `bytes_copy` wrap, owned RETURNs stay AST behind the deferred `bytesview_to_bytes` coerce); iteration (the for-each gate's explicit bytes exclusion lifted -- names + F1-record fields, a plain `uint8_t` typed-copy loop var, zero new emit); and concat/aug-assign (`a + b` admits the template-less @native `__add__` through the native binop arm; `t += v` on owned-bytes LOCALS desugars to the concat-and-assign `t = ::tpy::bytes_concat(t, v);` -- no bytes `THIRStrAppend`, there is no in-place append). Aug-assigned bytes PARAMS stay AST: the span param silently rebinds to the concat's dying temporary -- a silent-dangle AST miscompile, filed as the bytes face of the str aug-assign-param BUGS.md entry. +7 bodies on the merged tree (no solo run -- the cell gates were serialized).
- **S4 leftovers wave 2 landed (increment 47)** -- slice-object ctor LOCALS (`sl = basic_slice(1, 3)` / `slice(a, b, c)`: `_slice_ctor_call_eligible` over a factored `_template_init_call_fi` shared with the scalar-ctor gate; `None` bounds lower to STORAGE-form `THIRLiteral` -> `std::nullopt`; no Optional call-arg cascade needed -- sema rejects BigInt bounds at the ctor, so literal/name/None covers everything valid), Char record fields (field reads, `_scalar_field_write_ok`, the field-assign lowering arm, and the ctor MIL scalar arms widened to `_eligible_char`; str-literal rejects are defensive, sema type-errors those), call-result ITERABLES (`for c in full(s):` -- a new `THIRForEach.iterable_lvalue` fact drives the `auto` vs `auto&` capture, `[rvalue]` in dump), and non-name char-subscript receivers (the subscript gate now shares `_str_slice_receiver_ok`). Deferred: slice-ctor rvalues as call args, container-returning call iterables. Corpus routing unchanged (27561 / 3280 -- the shapes occur nowhere in otherwise-routable corpus bodies; units are the net, the ledger convention). Increments 46-48 were parallel cells integrated sequentially alongside the main-thread U1 (increment 49); the INTEGRATED tally on the merged tree is **28774 bodies / 3280 cases** (+1213 over the 27561 base -- the methods-axis +1206, the bytes tail +7, S4 wave 2 and U1 unit-carried).
- **F4 U2 pointer-variant conversions landed (increment 50)** -- the union form rung proper, for F1-record-member pointer unions (`A | B` -> borrow `std::variant<A*, B*>` / storage `std::variant<A, B>`): the `THIRFormConvert` union arms mirror `context.convert`'s (`to_[const_]ptr_variant` at BORROW, const from the receiver like the F1 OPTIONAL_TO_PTR bump; `to_value_variant<...>` at STORAGE); `LocalBinding.PTR_VARIANT` locals (a bare borrow copy of a same-union name, or the field-lvalue lift; name-source reseats route, field reseats stay AST -- a per-reseat const chain); union-field writes from borrow names through the existing F2b lowering; bare borrow passthroughs (params / same-type args / borrow returns; readonly slots stay AST -- the `ptr_variant_to_const` wrap); and the ctor MIL own-param move (`u(std::move(v))`, `Own[A | B]` admitted at the ctor param gate). WITH the rung, the validator's sink table (pointer-lifted field-write/MIL sinks reject BORROW values; BORROW returns need a borrow-legal type) and the `THIRBytesLiteral.owned` -> form fold; the sink rule's first live catch was the `Own[...]`-param BORROW mislabel (own params now read STORAGE -- they own their storage), and the corpus byte-diff's was the MEMBER-record-name return (`return d` takes the AST's `&(d)` lift; the return gate now admits same-union names only, `prescan.ret_ptr_union`). Gate-rejected pre-existing AST miscompiles (BUGS.md, one entry, three sibling shapes): storage union fields at returns (`&(h.u)`) and call args (bare `h.u`), const-lifted locals into mutable-pointee slots. Routing 28774 -> **28778 bodies / 3280 cases** (+4 -- corpus union code narrows, the U3 cell)
- **F4 U3 isinstance-narrowing reads landed (increment 51)** -- the stateful narrowing emit, where corpus union routing lives: `isinstance(v, A)` / `isinstance(v, (A, B))` conditions over routed unions (U1 value / U2 pointer-variant) lower to `THIRIsinstance` (`std::holds_alternative<M>(v)` per check member, OR-joined; template args carry the ptr-variant `*` and the U2 const-pointee chain), and a concrete-member branch fact prepends a `THIRNarrowAlias` extraction (`auto& __v = *std::get<A*>(v);` ptr / `const auto& __v = std::get<T>(v);` value -- `const auto&` for value-union PARAMS, mirroring the param-const qualifier rule) with reads inside the scope renamed via a lowering-time `lc.narrowed` map (the `ctx.narrowed_vars` mirror; the condition deliberately keeps the original variant). The full `_gen_if` shape set is mirrored: the 2-member else complement alias (the alias node carries else_body[0]'s loc so `emit_else_comment`'s backward scan finds the `else:` line), the flat `else if` elif chain, the chain BREAK when the outer else-fact is concrete (`THIRIf.else_is_nested` -> `} else {` + a nested if, the `_has_concrete_isinstance_facts` gate), the exhaustiveness constant-fold (`macro_expansion == True` -> a bare `if (true)` with the dead implicit-else suppressed), and the early-return implicit else (a persistent statement-level alias appended after the `if` via the new `_lower_stmts` chokepoint, subject retyped for the rest of the enclosing scope; scope save/restore around branch/loop bodies mirrors the AST's `narrowed_vars` snapshots, incl. the persistent-alias suffix bump). Eligibility threads a `narrowed` set through the body walk: branch bodies check with the subject RETYPED to the member (so reads/field-writes gate like record params), rebinding writes to a narrowed subject reject, and remaining-union facts (tuple-form checks) keep the subject un-extracted on both paths. Gate-rejected (deferred rows): `while isinstance` loop-entry extraction, `assert isinstance` persistent narrowing, compound (`and`/`or`) conditions, re-dispatch on an already-narrowed subject, readonly subjects (the U2 `ptr_variant_to_const` verdict), narrowed record call-args and method receivers (the record call-site frontiers), Any/polymorphic/deref-view subjects. Routing 28778 -> **28788 bodies / 3280 cases** (the narrowing functions themselves; most isinstance bodies also contain still-gated constructs -- record method calls, `match`, union prints).
- **F4 U2 write-arm tail landed (increment 52)** -- None-member pointer-variant unions: `_eligible_ptr_union` admits void-like members (monostate spells the same in borrow and storage form), so `w = None` decls/rebinds, `recv.field = None`, and `return None` at a ptr-union slot all render `std::monostate{}` via the existing target-typed None literal; plus union field-to-field copies (`recv1.f = recv2.f`, a plain storage-to-storage assign -- a field access is not a ptr_variant_source, so no `to_value_variant` lift). Solo routing 28861 -> 28873 bodies / 3285 cases (+1 corpus case, `union_ptr_variant_none_write`).
- **F4 U4 narrowing tail landed (increments 53-54)** -- (53) `while isinstance(v, A)` routes with the loop-entry extraction as a `THIRNarrowAlias` leading the `THIRWhile` body (branch-alias shape, popped at the brace; a body assert on the same subject stays AST -- the AST redeclares the alias there, the BUGS.md `_gen_while` collision), and `assert isinstance` routes via the new `THIRAssert` node (`if (!(<cond>)) ::tpy::raise_assertion_error(...)`, None/str-literal messages; the persistent alias appended by `_lower_stmts` like the post-if alias; a re-assert on a persistently extracted subject mirrors the sema fold `if (!(true))` + the suffix-bumped `__v_2` re-extraction, gated by `_WalkState.persistent_narrowed` / its `_LowerCtx` mirror + `narrow_subject_union`). (54) Compound `and` conditions in if/while/assert position: `_compound_narrow_info` admits one isinstance leaf in the `&&` tree, other leaves eligible bool conditions; subject reads after the leaf lower to `THIRNarrowedRead` (the alias-free `(*std::get<A*>(v))` / `std::get<T>(v)` condition-position get), installed condition-scoped via `lc.inline_narrowed`. `or` trees, multi-subject compounds, and compound re-asserts stay AST. Routing 28861 -> 28871 bodies / 3284 cases.
- **S4 leftovers wave 3 landed (increment 55)** -- container-returning call iterables (`for x in make_list():` -- `_call_eligible` grows `container_ret_ok`; the capture verdict rides `THIRForEach.iterable_lvalue` via `_call_iterable_lvalue`, mirroring `is_lvalue_iterable`'s call arm: `Own[...]` return -> owning `auto __obj_N =` rvalue capture, borrow/readonly return -> `auto&` lvalue alias) and slice-object ctor rvalues as call args (`use(s, basic_slice(1, 3))` -> the bare template expansion into the by-value slot via `_slice_ctor_pass_through_arg`; Own-wrapped and union slots stay rejected). Solo routing 28861 -> 28877 bodies / 3286 cases (+2 corpus cases). Integrated with increments 52-54 on the same branch: **28899 bodies / 3287 cases**.

**Increment 1 (`tpyc/thir/`) productionizes that spike** for the non-form
value-scalar slice: a fixed-width-int, non-method, non-generic function whose
body covers names, literals, same-width integer arithmetic (the
`c = a + b + 1` spike), same-module free-function calls
(`helper(a) + helper(a + b)`), the literal-into-typed-slot coercion,
`if`/`elif`/`else` over comparison conditions, and `while` loops -- lowering to
immutable THIR and emitting its body from THIR with no analyzer reference,
byte-identical to the AST path (elif chains flatten to `else if`; source/else/
trailing comments reproduced). A local's declared type is captured onto
`THIRVarDecl` via codegen's own `resolve_stmt_binding_type`, so sema's local
deduction -- e.g. a literal-seeded `offset = 0` that retro-widens to UInt64 from
later usage -- lowers with the right type rather than the bare IntLiteralType.
Enabled per-function behind `--thir-codegen` (or `TPY_THIR_CODEGEN=1`); off by
default, so production output is untouched. The eligibility gate in `lower.py`
is the safety boundary -- it rejects every construct the emitter cannot yet
reproduce (globals; imported/cross-module + generic/overloaded-arity calls;
non-scalar types; widening/other coercions; `for` loops; `while`/`else`;
`break`/`continue`; branch-local first-declarations; any function whose locals
hoist out of a branch; narrowing conditions), which stay on the AST path. The
whole test corpus passes with the flag forced on (zero snapshot diffs).
`uv run pytest --thir-codegen --no-exec` is the first-class harness gate that
runs this byte-diff over the whole corpus; it refuses `--update-snapshots` (and
the `UPDATE_EXPECTED=1` env spelling), so THIR output can never silently become
the AST baseline it is checked against. The per-case byte-diff covers a case's
local modules only (where the eligible shapes live); stdlib bodies route through
THIR too but their generated C++ is not snapshotted per case, so the byte-diff
does not cover them -- `--thir-codegen --force-exec` builds and runs the
THIR-rendered stdlib, catching behavioral (not byte) divergence there. The gate
self-checks against vacuity: it tallies the bodies actually lowered through THIR
and a full forced run that routes zero -- the flag silently stopped reaching
codegen -- fails loudly (`tpy| thir: N bodies routed ...` otherwise). A filtered
run (`-k` / explicit path) only warns, since a subset may hold no eligible case.

**Increment 2 adds range-`for`**: a `for v in range(stop)` / `range(start, stop)`
counter loop with step 1, over a fixed-int loop var that is not used after the
loop (no `for/else`, no `break`/`continue`), bounds restricted to a bare int
literal (inlined) or a bare name (hoisted into a `__start_N`/`__stop_N` temp).
It reproduces `_gen_range_counter_loop`'s plus_one / non-hoisted branch
byte-for-byte. This required the emitter's first piece of per-function state: an
`iter_counter` reproducing `ctx.iter_counter`, sound because (verified across
codegen) only range-`for` bumps that counter within the eligible slice and it
resets per function. Deferred to the AST path (filed in TODO.md): 3-arg/stepped
ranges, hoisted loop vars, `for/else`, non-range iteration, bounds that are
binops/calls/`Int32(k)`/negated literals, loop vars shadowing an outer local,
and non-fixed-int element types.

**Increment 3 adds double `float`** as a scalar type alongside fixed-ints:
params/returns/locals, names, finite float literals (rendered `repr(value)`,
byte-identical to `_gen_float_literal_value`'s double branch), and float
arithmetic/comparisons. Float arithmetic (`+ - * //`) and comparisons need no
new emit code -- they already flow through the same templated binop path as int
(the dunder's `cpp_template` + operand wrappers differ, but the emitter reads
them generically). Float `/` (true division -> `tpy::truediv`) is auto-excluded:
it lacks a `resolved_binop` `cpp_template`, so the existing `_binop_eligible`
gate rejects it (this also keeps int `/` out, same reason). `Float32` is excluded
-- its literals need a `f` suffix the slice does not emit.

**Increment 4 adds first-class `bool`** (the safe subset): bool
params/returns/locals, `True`/`False` literals, and **bare-bool conditions**
(`if flag:` -- previously a condition had to be a comparison). Deferred to the
AST path (filed in TODO.md): `and`/`or` (the `&&`/`||` narrowing +
short-circuit-slot emit path in `_gen_logical_value`) and `not` (the
`resolved_unaryop` emit path).

**Increment 5 adds comparison-as-value** (`x = a < b`, `return a == b`): a
comparison is now admitted in any value position, not just an `if`/`while`
condition. This required **resolved-local-type tracking** in lowering -- the
eligibility/lowering walk now threads a `name -> resolved type` map (was a bare
declared-name set), because mixed-sign fixed-int comparisons emit `std::cmp_*`
(not a bare operator) and must be excluded, but the mixed-sign decision needs
codegen's resolved operand types: a retro-widened literal-seeded local
(`offset = 0` later used as `UInt64`) is `Int32` under `analyzer.get_expr_type`
but `UInt64` under codegen's `get_resolved_type`, so a naive analyzer-type check
over-excludes it. The map carries the var-decl's resolved (retro-widened) type,
matching codegen, so same-sign comparisons route and only true mixed-sign ones
are excluded. Next slices: `and`/`or`/`not`, then the form decision (Open Q
9/11/12) gates the form-carrying nodes (tuples/unions/non-value locals).

**Increment 6 lands form rung F1** -- the first form-carrying slice. It admits
single-assignment non-value **record** locals bound from a field read (the two
binding shapes `T&` alias and `T*`-via-`optional_to_ptr`), **scalar field reads**
off F1-record receivers (params or `T&`-alias locals), and F1-record reference
params (same-module, non-native, non-generic; readonly free functions excluded).
The form facts are carried on the IR: `Form` (BORROW/STORAGE/VALUE, default
VALUE) on every `THIRExpr`, a `THIRFormConvert` node for the explicit
storage->borrow lift (no `kind` field -- the helper is a pure function of the
type family + direction + const + move), and `THIRFieldAccess`. The
binding-shape decision is the shared `forms.classify_local_binding` that the
legacy AST path now also calls (the T*-vs-T& choice in `_gen_var_decl_code`),
so lowering and codegen cannot drift. `is_const` for the optional read is a pure
sema read (`FunctionInfo.const_borrow_params` + `ReadonlyType`), confirming the
F1 gate's pure-classifier criterion. An F1 borrow local's decl type renders
through codegen's `type_to_cpp` (threaded into lowering, run after the
module's `native_cpp_names` are registered) -- `TpyType.to_cpp()` would
mis-qualify cross-module/live-module records. Deferred to the AST path (filed in
TODO.md): reassigned/rebound non-value locals (F2), container/tuple/union locals
(F3/F4), cross-module / native / generic records, subscript and name-alias
sources, `->` pointer-local receivers, and any call passing a non-value
(record / `Own[record]`) argument (the auto-move `f(std::move(p))` the bare-name
THIRCall emit does not reproduce). Byte-identical across the whole corpus with
`--thir-codegen` forced.

**Increment 7 lands the F2 core** -- the reassigned/rebound and write halves of
the form rung, both restricted to their byte-reproducible, gate-clear subset.
*F2a:* a reassigned plain-record local lowers to a reseatable `T*` pointer-local
(`POINTER`, the reassigned counterpart of F1's REF_ALIAS) when every assignment is
an lvalue field source -- init/reseat as `&(...)` (a STORAGE->BORROW
`THIRFormConvert`, the same node F1 uses for the read), reads as `recv->field`. The
shared `forms.classify_local_binding` gains `POINTER`; the AST path branches only
on `is not REF_ALIAS`, so the split is transparent to it. *F2b:* a borrow `T*`
(POINTER or `optional_to_ptr` local) stored into a storage `Optional[record]` field
lowers to `recv.field = ::tpy::ptr_to_optional(p)` -- the first **mutating**
statement in the slice, so the written receiver becomes a non-const param (a pure
`const_borrow_params` read, the same fact F1 uses). A pre-commit gate confirmed the
copy-vs-move choice is a pure sema read (`function_movable_locals` + last-use) and,
for a borrow source, `_move` never fires -- so F2b emits the copy helper
unconditionally. Deferred to the AST path (filed in TODO.md): the rvalue
rebind-slot (`__slot_N`) machinery, `_move`/owned sources, `copy()`-acknowledged +
call/None/name-alias write sources, return-into-storage-optional, ctor-MIL, and
call-arg. Byte-identical across the whole corpus with `--thir-codegen` forced.

**Increment 8 finishes the F2 remainder** -- the buildable form cells deferred by
increment 7, in three gated sub-rungs. *F2c:* the storage-form `Optional[record]`
return (`Own[T] | None`) -- `return <borrow T*>` lifts via `ptr_to_optional`
(copy), `return None` and `recv.field = None` lower to a STORAGE-form `None`
literal (`std::nullopt`, the literal's `form` selecting nullopt vs nullptr at
emit). *F2d:* the rvalue rebind-slot machinery -- a reassigned plain-record local
whose source is an rvalue (a ctor / by-value call) lowers to the two-slot
`__slot_N` form (`T __slot_n = init; std::optional<T> __slot_{n+1}; T* p =
&__slot_n;`, reseat `p = &*(__slot_{n+1} = rvalue)`). A new
`forms.LocalBinding.REBIND_SLOT` (transparent to the AST's `is not REF_ALIAS`
branch) drives it; the `__slot_N` numbering is a per-function `_EmitState` counter
(only a REBIND_SLOT decl bumps it within the slice -- every other `__slot_N`
consumer is gated out). *F2e:* the move half -- an owned (REBIND_SLOT) source at
last use lifts via `ptr_to_optional_move`, decided at lowering from the same
`function_movable_locals` + `all_last_uses` facts the AST reads (`THIRFormConvert.
move`). At F2's landing all three routed zero corpus cases (these shapes live in
methods / cross call boundaries in the corpus), so the
`tpyc/thir/test_thir_*.py` unit tests were the per-rung net (routing +
byte-identity); increment 9's method frontier (below) since routes them under the
whole-corpus net. Byte-identical across the whole corpus with `--thir-codegen`
forced. Still deferred -- **not form cells but separate frontiers**: ctor
member-init-list (the MIL is emitted by the record driver outside `gen_body`, and
only for constructors -- the method frontier and a THIR `self` model landed in
increment 9, so this now needs only the ctor-specific MIL node + the ~190-line
field-init hoist) and the call-arg / call+`copy()`-write `ptr_to_optional` sites
(admitting a non-value call arg forces the whole arg-coercion cascade incl.
auto-move `f(std::move(p))`, so they must land together, not as an isolated form
cell).

**Increment 9 opens the method frontier (M1)** -- a callable-kind axis orthogonal
to the form ladder. The slice was free-functions-only, so every F1/F2 rung routed
**zero corpus cases** (records/optionals live in *methods*); admitting instance
methods puts F1/F2 under the whole-corpus byte-diff gate over real code. An
instance method's receiver is modeled as an F1-record **pointer-local** (`self` ->
the C++ `this`, arrow field access) via a dedicated `THIRSelf` node (`this` is a
keyword `escape_cpp_name` would mangle); the body reuses the F1/F2 lowering+emit
unchanged, and only the body -- not the AST-emitted signature -- routes. A readonly
method's `self` is const (seeded into `const_locals`, so an `optional_to_ptr` off
`self.opt` lifts to `const T*`); admitting readonly methods is load-bearing, since
a non-mutating getter is auto-readonly. Scope: a same-module non-generic record's
plain instance methods with **value-scalar params** (record params -- M2 -- need a
method-level const-param verdict the free-function registry can't supply). The
feed list (`iter_module_callables`) is shared by `lower_module` and codegen so the
two never drift; the constructor is excluded (its body is emitted via the
member-init-list driver, not `gen_body` -- the M3 ctor-MIL frontier). Deferred
(AST path, filed in TODO): M2 record params + the const-self-receiver model for
them; M3 constructors / member-init-list; static/property/dunder-operator methods;
generic-record methods; nested-record methods. (Scalar-`self`-field writes, also
deferred by M1, landed in increment 10 below.)

**Increment 10 adds scalar field writes** -- `recv.field = <scalar>` off any
F1-record receiver (param / `self` / pointer-local), the value-scalar sibling of
the F2b optional-field write. A scalar field is value-form, so it lowers to a
plain `THIRAssign` (no borrow<->storage `THIRFormConvert`) and emits the AST's
default field-assign (`recv.field = <value>;`); the receiver renders `.`/`->`
exactly as a field read does. Eligibility reuses `_field_receiver_ok` plus an
exclusion of the write-side property-setter / `__setattr__` markers it does not
cover.

**Increment 11 adds scalar augmented assignment** -- `x += y` / `recv.field += y`
on a scalar local or F1-record scalar field. Lowers to `target = (target OP value)`,
reusing `THIRBinOp` (its emit already mirrors `_gen_binop_from_result`) wrapped in
a `THIRAssign`; the target expr is lowered twice (assign lvalue + binop left
operand), matching the AST substituting the same target string into both slots.
A new `THIRBinOp.paren_wrap` flag (False here) reproduces the AST aug-assign path's
omission of the precedence parens an expression-position binop adds (`x = a + b;`
vs `x = (a + b)`). The synthetic binop carries `divisor_non_zero=False` -- the AST
aug-assign path never swaps `div_check`->`div_floor` (no source `TpyBinOp` node
carries the flag). Eligibility (`_scalar_aug_assign_ok`) gates out every AST
preprocessing branch the substitution can't reproduce: in-place dunders
(`resolved_inplace`), a missing/non-template `resolved_binop`, `str +=` (excluded
for free -- a str target is not an eligible scalar), `FixedInt += BigInt` (the AST
inserts `.to_fixed_check<T>()`), and subscript / class-constant / narrowed-optional
targets.

**Increment 12 adds record params on instance methods (method frontier M2)** --
M1 admitted methods with value-scalar params only; M2 admits F1-record (`T&` /
`const T&`) params, like a free function. The param's const-ness is read from the
method's own `FunctionInfo.const_borrow_params`, looked up on the owning record
(`get_record(name).get_method_overloads(...)`) -- the method-side analogue of the
free-function registry lookup, mirroring codegen's `_get_method_mutated_params`.
The body consumes it for the OPTIONAL_TO_PTR const bump (`_f1_is_const`), so a
record param threads through `_LowerCtx.record_name`. No readonly carve-out: for a
plain F1-record (ref) param the readonly forced-const verdict and the inferred
`const_borrow_params` verdict coincide (`decide_param_const` returns const iff the
param is not directly mutated / address-escaped in both modes), so the inferred set
is exact for readonly methods too -- a carve-out would only re-exclude the common
auto-readonly getter the rung exists for. Routing 2835 -> 3163 bodies across 534 ->
576 cases. Deferred: generic-record / static / property / nested-record methods;
`deep_const_borrow_params`.

**Increment 13 admits readonly free functions** -- M1 excluded any
`func.is_readonly and not is_instance_method` (a `@readonly` free function) on the
same worry the M2 carve-out turned out not to need. The exclusion is removed: a
readonly free function's F1-record params are read via the (free-function)
`const_borrow_params` like any other, and the forced/inferred verdicts coincide
exactly as for readonly methods (a readonly callable cannot mutate a param, so its
record params are uniformly const). Byte-identical; small corpus gain (3163 ->
3165 bodies / 576 -> 577 cases -- most readonly free functions stay AST-side for
other reasons: native, generic, non-F1 params, or calls in the body), but it
removes the last readonly asymmetry in the eligibility gate.

**Increment 14 opens the ctor frontier (M3a)** -- a constructor's member-init
list is emitted by the record driver (`gen_record_decl`) *outside* `gen_body`, so
unlike a method (M1) it cannot reuse the `gen_body` THIR hook. M3a adds a
whole-ctor `THIRConstructor` node (member-init-list split from body), a dedicated
MIL-tail emitter (`emit_thir_constructor_tail`), and a separate ctor feed
(`iter_module_constructors` -> `lower_constructor` -> `ctx.thir_constructors`);
the signature stays on the AST path (the M1 precedent -- only the ` : f(v)... {}`
tail routes). The slice is **pure-MIL scalar, flat record**: a same-module
non-generic record with no base class whose `__init__` is entirely hoistable
own-scalar field inits (`self.<scalar field> = <eligible scalar>`), so every init
hoists to the MIL and the emitted body is `{}`. No demotion mirror is needed -- a
corpus survey found a non-init ctor body in <1% of constructors (the M3c rung), so
M3a needs only the hoist half plus a reject gate. A docstring or `pass` lands in
the AST's non_init_stmts (a codeless but non-empty `{\n ... }` body, not `{}`), so
it is rejected too (M3c). Routing 3165 -> 4652 bodies / 577 -> 1160 cases (+1487
constructors; the non-vacuity tally now counts ctors). All remaining ctor work is
on the path to deleting `_extract_field_inits` / `_extract_base_inits` /
`_get_non_init_stmts` + the inline MIL emit -- that deletion (corpus still
byte-identical) is the **ctor-frontier completeness gate**: **M3b** record /
`Optional[record]` MIL fields (the `ptr_to_optional` cell) + the MIL-specific
own-param move; **M3c** the non-init body + the full hoist/demotion mirror (with
hand-written cases per demotion branch -- the corpus exercises it in <1% of ctors,
so routing volume does not validate it); **M3d** base inits (inheritance);
`str` / `list` / `dict` / `tuple` / `union` fields ride the form ladder (F3+);
generic / native records ride their own frontiers.

**Increment 15 extends the ctor frontier to record / Optional[record] MIL fields
(M3b-copy)** -- the borrow->storage *copy* arm, reusing the F2b/F2c form machinery
in member-init-list position with no new emit node. A pointer-repr
`Optional[F1-record]` field routes the F2b optional-write helper (`None` ->
`std::nullopt`; a non-own borrow source -> `::tpy::ptr_to_optional(p)`, the cell
that originally motivated M3); a plain `F1-record` field admits a non-own record
param (`copy()`-unwrapped) as an implicit MIL copy (`f(p)`). The ctor param gate
gains pointer-repr `Optional[F1-record]` (`_ctor_param_eligible`) so the cell's
`m: Inner | None` param is admittable; the MIL value is built per field type
(`THIRFormConvert` STORAGE / STORAGE `None` literal / plain copy) by
`_lower_ctor_mil_init`. Own-param sources (a `std::move`) are the **M3b-move**
rung; ctor-call rvalue sources are **M3b-rvalue**; field-read sources defer. Small
gain (4652 -> 4691 bodies / 1160 -> 1169 cases, +39 ctors -- most record-field
ctors take `Own` params, so they land in M3b-move), but it reaches the
`ptr_to_optional` cell and establishes the non-scalar-MIL-field pattern as a pure
F2b/F2c reuse. Byte-identical (`--thir-codegen --no-exec` green).

**Increment 16 adds the ctor MIL move arm (M3b-move)** -- an own-param (`Own[T]`)
source consumed at its last use moves into a record / Optional[record] field
(`f(std::move(p))`), the common ownership-taking ctor. This is THIR's first
`std::move` emit on a bare value (a `move` flag on `THIRMilInit`); `_is_move_source`
is generalized to take the movable-name set (mirroring the AST's parameterized
`_is_last_use_movable`), the ctor MIL passing `own_param_names`. The move arm never
combines with `ptr_to_optional`: lowering checks move first and returns a plain
source before the Optional arm (matching the AST cascade, where the AST's
`source_is_own_optional` flag and an own-record's non-Optional `val_type` both skip
`ptr_to_optional`). Small gain (4691 -> 4703 bodies / 1169
-> 1172 cases, +12 -- most `Own`-param records are entangled with deferred features:
a non-init body, a base class, a generic record, or a non-F1 field). Byte-identical
(`--thir-codegen --no-exec` green).

**Increment 17 finishes the ctor MIL value-source arm (M3b-rvalue)** -- the record
*value* sources that construct a field (or its `Optional`) directly, factored into
one `_is_record_value_source` predicate: a non-own F1-record param name, an F1-record
ctor-call rvalue (`self.rec = Inner(v)`, reusing `_is_record_rvalue_source`), and an
F1-record field-read off a *param* receiver (`self.rec = b.inner`). For an
`Optional[F1-record]` field these construct the optional directly (`opt(Inner(v))`) --
`ptr_to_optional` is reserved for borrow-`T*` sources, so `_lower_ctor_mil_init`
splits the Optional arm on `_is_borrow_ptr_local`. `_ctor_param_eligible` gains the
own-optional shapes (`Own[Inner | None]` / `Optional[Own[Inner]]`, peeled via
`unwrap_optional_own`), which move into the Optional field through the existing move
arm. `copy()` is unwrapped before the Optional check too, closing the record/Optional
asymmetry (`self.opt = copy(m)` now routes like `self.rec = copy(p)`). `self.<record
field>` read sources stay deferred (their pointee may be uninitialized at MIL time --
ordering-sensitive). Gain 4703 -> 4739 bodies / 1172 -> 1182 cases (+36). Remaining
ctor work: M3c (non-init body + hoist/demotion mirror); M3d (base inits); F3+ field
types. Byte-identical (`--thir-codegen --no-exec` green).

**Increment 18 opens the ctor body (M3c-trivia)** -- the first non-empty ctor body:
docstring / `pass` non-init statements. They emit no C++ (a docstring's `gen_body`
code is None, `pass` is `""`), so they break no hoist chain -- every field init still
hoists to the MIL -- but their *presence* keeps the body non-empty (` {\n    }`, not
` {}`). THIR's first body-statement shape, `THIRNoOpStmt`: lowered for `pass` (keeps
its `loc` so `_emit_stmts` emits the `// pass` source comment) and a docstring (lowers
with `loc=None` -- the AST emits NO comment for a docstring, its simple-stmt code being
None, so loc-suppression matches byte-for-byte). The emitter's pre-built `if ctor.body:`
brace branch (from M3a) carries it. Gain 4739 -> 5030 bodies / 1182 -> 1210 cases (+291
-- docstring/`pass` ctors are common). Remaining ctor work: M3c-demotion (the ~190-line
`_extract_field_inits` hoist/demotion mirror + non-trivia body statements, bounded by
the statement-shape axis); M3d (base inits); F3+ field types. Byte-identical
(`--thir-codegen --no-exec` green, source comments on).

**Increment 19 completes the ctor body (M3c-demotion)** -- the hoist/demotion split:
a field init demotes into the body (with the non-init statements) when it can't hoist.
Key finding studying `_extract_field_inits`: THIR's existing `_ctor_field_init_ok` gate
(declared = params + self) already subsumes the bare-name / body-local / nested-def /
temp-rollback demote triggers -- a field init whose source isn't resolvable from params
isn't hoistable, so it naturally demotes -- and `_body_eligible` rejects any demoted init
it can't lower (the whole ctor falls to AST, byte-safe). So the only trigger needing
explicit reproduction is the **`chain_broken` ordering cascade** (a hoistable init after a
non-init statement must demote, since the MIL runs before the body). `lower_constructor`
now splits the body into (hoisted MIL inits, body stmts) and lowers the body via the same
`_body_eligible` / `_lower_stmt` machinery method bodies use -- no new node/emit. Bounded
by two parked limits: the statement-shape axis (only already-eligible body statements) and
demoted *record*-field writes (`_stmt_eligible` admits only scalar / Optional field
writes). Tiny routing gain (5030 -> 5031 bodies / 1210 -> 1211 cases, +1 -- the corpus
exercises body-eligible demotion in <1% of ctors, so 5 hand-written unit cases per behavior
are the real validation), but it reproduces the demotion mirror, the completeness gate for
deleting the AST ctor MIL emit. Byte-identical (`--thir-codegen --no-exec` green).

**Increment 20 opens ctor inheritance (M3d-1)** -- a single same-module F1 base routes:
its `super().__init__(args)` lowers to a `THIRBaseInit` (base C++ name + lowered arg
exprs, the placeholder `base_inits: tuple[str]` becoming structured) prepended to the
field MIL, mirroring the AST `_extract_base_inits` `Base(args)` render. The base-init
breaks no hoist chain; the rest of the body reuses M3c-demotion. The derived signature
stays on the AST path (M1 precedent) and is identical to a flat one -- only the tail
carries the base init -- so the existing `gen_record_decl` tail seam works unchanged.
The load-bearing guard: an inherited-field write (`self.<base field> = expr`) goes to
the body *without* breaking the chain in the AST (the base ctor owns the MIL slot),
which the M3c-demotion chain logic can't reproduce, so M3d-1 rejects any such ctor (and
multi-base + the explicit `BaseN.__init__` form + non-F1 bases) to the AST path. Gain
5031 -> 5057 bodies / 1211 -> 1216 cases (+26 -- single-base ctors are common).
Byte-identical (`--thir-codegen --no-exec` green).

**Increment 21 finishes ctor inheritance (M3d-2)** -- multiple bases + inherited-field
writes, completing the constructor self-contained tail. Multi-base: every
`super().__init__` / `BaseN.__init__` call lowers to a `THIRBaseInit`, collected and
sorted by parent declaration order (mirroring `_extract_base_inits`'s -Wreorder-safe
ordering); the single-base gate relaxes to "every parent is a same-module F1 record."
Inherited-field writes: a direct `self.<base field> = expr` goes to the body (the base
ctor owns the MIL slot) without breaking the hoist chain, tracked in
`body_written_self_fields` so a later own-field hoist that reads it demotes (the
`expr_reads_self_field` trigger) -- completing the `_extract_field_inits` walk; the M3d-1
guard that rejected such ctors becomes that body branch. The `_reject_nondef_ctor_field_in_body`
error path is not reproduced -- it fires only for a demoted own non-default-constructible
(record-typed) field, whose body-write is not body-eligible, so such ctors reject first.
Gain 5057 -> 5071 bodies (+14, same 1216 cases). Byte-identical (`--thir-codegen --no-exec`
green). **The ctor frontier (M3a-M3d) is complete; remaining ctor cells are cross-axis-blocked
(F3+ field forms, the record body-write rung, native/generic-record frontiers).**

**Increment 22 opens form rung F3 (tuple form), read side** -- the first
type-family rung past the optional/record slice, and the start of the form-ladder
spine toward F-final. A borrow-form pointer-repr tuple return (`tuple[..., Ref]` ->
`std::tuple<..., T*>`) lifts a storage tuple field read via `tuple_to_pointer`, the
F2c-return analog for the tuple family. The `THIRFormConvert` node already models
this by design (no new node -- the helper is a pure function of result_type family +
direction + const); F3 adds the emit branch (mirroring `context.convert`'s tuple
BORROW arm byte-for-byte, `tuple_to_pointer<to_cpp_return[_const]()>`) and the
lowering: `_f1_tuple` (a pointer-repr tuple whose every element is an eligible scalar,
an F1-record, or a pointer-repr `Optional[F1-record]`, so `to_cpp_return()` / `to_cpp()`
recursion never hits a cross-module/native/generic element where bare `to_cpp()` would
mis-spell), the `ret_borrow_tuple` prescan slot, and the return eligibility + lowering.
Element const-ness tracks the receiver (the F1 OPTIONAL_TO_PTR mirror). Gain 5071 ->
5078 bodies. Byte-identical (`--thir-codegen` green).

**Increment 23 lands F3's write side** -- a tuple-field write `recv.field = p` where
p is a borrow tuple param lifts borrow->storage via `tuple_to_storage` (copy), the
F2b/F2c-write analog. The lowering reuses the existing optional-field-write
STORAGE-convert path unchanged (the emit dispatches the helper from the result_type
family), so only the emit branch (`tuple_to_storage[_move]<to_cpp()>`) and the
eligibility (`_f1_tuple_field_write_ok` + borrow tuple params in `_f1_param_eligible`)
are new. Two correctness fixes the byte-diff surfaced: (1) the **M3 ctor frontier** now
keeps a ctor on the AST path when a clean leading own-field init the AST hoists into the
MIL can't be reproduced there (an F3+ field type or unsupported source) -- demoting it
into the body diverged from the AST's MIL hoist, previously masked because the demoted
write was body-ineligible and unmasked by the new tuple write predicate; post-chain-break
demotion (M3c) is unaffected. (2) A pointer-repr tuple **name** is tagged BORROW despite
`is_value_type()`, so a convert source is never mislabeled VALUE. Gain 5078 -> 5080
bodies. Byte-identical.

**Increment 24 completes F3's read slice -- storage-tuple alias locals** -- a
pointer-repr tuple local bound from a storage tuple field (`t = h.pair`) lowers to
`auto&& t = h.pair;` aliasing the source's storage, and a borrow consumer (`return t`)
lifts it via `tuple_to_pointer` like a direct field source. A shared
`forms.is_storage_tuple_alias_decl` predicate decides the arm (co-located with the
binding classifiers; the AST decides it inline in `_gen_var_decl_code`, the byte-diff is
the anti-drift net) + a THIR-only `STORAGE_TUPLE_ALIAS` LocalBinding emitting `auto&&`. A
`storage_tuple_locals` set threaded through the eligibility walk + `_LowerCtx` tracks the
aliases: the local reads as STORAGE form, the return-lift accepts it as a source, and the
borrow-tuple write source (`tuple_to_storage`) excludes it (it is storage form, a direct
copy). Const tracked from the source receiver. Gain 5080 -> 5081 bodies (most alias uses
co-occur with subscript / print, still parked). Byte-identical. **Still on the AST path
(later F3 cells):** Subscript / storage-Name alias sources, reassignable BORROW_TUPLE /
OPTIONAL_BORROW_TUPLE locals, the `Own[tuple]` `tuple_to_storage_move` move arm, subscript
/ loop-var / unpack tuple sources (the statement-shape axis), and tuple-literal MIL
construction.

**Increment 25 opens the statement-shape axis (tuple subscript reads)** -- the first
cell of the third migration axis (statement/expression shape), past the form-ladder and
callable-kind axes. A value-result tuple subscript `t[N]` lowers to `std::get<N>(t)` (a
new `THIRSubscript` node mirroring `THIRFieldAccess`; the index is the AST's compile-time
constant, negatives folded to a non-negative offset at lowering, mirroring
`_extract_compile_time_index`). The element is a value scalar, so the read is `VALUE`
form with no lift -- record / `Optional`-element reads (which yield a `T*` / `T&` flowing
into `->` / `.` member access) ride the next cell on this frontier. To carry routing the
cell admits pure value-scalar tuple params (`const std::tuple<...>&`: every element an
eligible scalar, a value type whose borrow and storage forms coincide, so no conversion
machinery), whose signature stays on the AST path (the M1 precedent); the value-scalar
slot of an already-routed pointer-repr `_f1_tuple` reads the same way. Gain 5081 -> 5085
bodies / 1218 -> 1221 cases (modest -- the axis accumulates routing across cells; this
lands the foundation the heavier ones build on). Byte-identical (`--thir-codegen` green
over the full corpus). **Still on the AST path (next subscript cells):** record /
`Optional`-element reads (borrow results), value-tuple locals (init-source / literal
construction), container subscript (`items[i]`, dynamic index / bounds), the write
position (`t[0] = x`).

**Increment 26 extends the subscript frontier to record-element reads** -- `t[N].field`
where element N is a plain F1-record (a `BORROW_REF` pointer-repr slot). The subscript
yields a borrow (`std::get<N>(t)` is a `T*` off a borrow-form tuple param, a `T&` off a
storage `auto&&` alias), consumed by a value-scalar member access. The `THIRSubscript`
carries `BORROW` form; the `->` / `.` decision mirrors `_tuple_subscript_yields_borrow_ptr`
on the element's `value_form()` -- a `BORROW_REF` pointer-repr element off a borrow source
reads `->`, while an `Own` / value element (held by value) or a storage-alias receiver
reads `.`. Read position only: the shared write / borrow-local-binding paths keep their
name-receiver gate, so subscript writes and standalone record binds stay on the AST path,
and `Optional[record]`-element member access (the null-check path) is the next sub-cell.
Gain 5085 -> 5087 bodies. Byte-identical -- the gate caught a mixed `tuple[Own[A], A]`
case whose owned element must read `.` not `->`, fixed by keying the arrow on the
element-form predicate rather than the receiver alone.

**Increment 27 completes the tuple-READ frontier -- Optional[record]-element member
access** -- `t[N].field` where element N is an unproven `Optional[F1-record]`
(`needs_optional_runtime_check`) -> `::tpy::deref_check(<T*>).field`. The subscript is a
nullable `T*` off a borrow tuple param, or a `std::optional<T>` off a storage `auto&&`
alias lifted to `T*` via `optional_to_ptr` (a STORAGE->BORROW `THIRFormConvert`, reused
unchanged). A `deref_check` flag on `THIRFieldAccess` renders the runtime null check;
`_subscript_result_form` decides the subscript's form (an Optional element off a storage
alias is STORAGE and lifted, off a borrow param it is already `T*`);
`_field_markers_clean(allow_optional_check=True)` admits the runtime-check marker while
still excluding chained-deref / narrowed-to / property shapes. Read position only. Both
paths converge on `deref_check` (never `deref_optional_check`) because a tuple subscript
pre-lifts to `T*`. Niche (0 -> 1 corpus cases: a new `tests/cases/tuple/
subscript_optional_field` closes the empty byte-diff net + adds runtime deref_check
coverage); 5087 -> 5089 bodies / 1221 -> 1222 cases. Byte-identical, verified by three
review agents (safety + codegen clean, including a built+run probe confirming the correct
None-panic). This **completes the tuple-read frontier** -- value + record + Optional
element reads all route. Remaining subscript cells (container `items[i]`, the write
position `t[0]=x`, standalone record/Optional binds and borrow returns) are separate
frontiers.

**Increment 28 completes the tuple-subscript FAMILY -- the write position** -- `t[N].field
= <scalar>` and `t[N].field += <scalar>` where element N is a plain F1-record ->
`std::get<N>(t)->field = ...;` (arrow off a borrow tuple param) / `.field = ...;` (dot off
a storage `auto&&` alias). The target renders identically to the record-element read, so no
lowering change was needed -- the assign / aug-assign lowering already routes the target
through `_lower_expr`; the read predicate `_field_over_subscript_read_ok` is renamed
`_field_over_subscript_ok` (position-neutral -- one AST-unified fact, not a new one) and
added to `_scalar_field_write_ok` + `_scalar_aug_assign_ok` alongside the name-receiver
gate. Optional-field / property / value-element (`t[0]=x`) / readonly writes stay on the AST
path (markers / sema rejection). Niche (0 -> 1 corpus cases: a new
`tests/cases/tuple/subscript_field_write` writes through an aliased record and observes the
mutation -- output 8, forcing the value-vs-reference distinction on the cpy-parity check);
5089 -> 5092 bodies / 1222 -> 1223 cases. Byte-identical, reviewed by three agents (codegen
+ architecture clean). **Completes the tuple-subscript family** (reads + writes). Deferred
(the natural next subscript cell): Optional-field / tuple-field writes THROUGH a subscript
(`t[N].opt = None` / `t[N].tpl = <borrow tuple>`), needing the same `_field_over_subscript_ok`
addition on `_f2b_optional_field_write_ok` / `_f1_tuple_field_write_ok`. Beyond the tuple
family: container subscript (`items[i]`), for-loops, tuple-unpack.

**Increment 29 opens container subscript reads** -- the second statement-shape frontier
after the tuple-subscript family. A scalar-element container param (`list[scalar]` /
`dict[fixed-int, scalar]`, its signature emitted by the AST path per M1) read by subscript
routes its body: `c[i]` lowers to the checked dunder `::tpy::__getitem__(c, i)` (the
dominant form -- a param's length is unknown, so even a literal index is not bounds-safe),
or `c[static_cast<std::size_t>(i)]` when sema's value-range analysis proved the index
in-bounds. This is where the `THIRSubscript` node reaches its **canonical shape**: `index`
generalized from a compile-time `int` (the tuple `std::get<N>` specialization) to a
`THIRExpr`, plus a `bounds_safe: bool` field; `_emit_subscript` dispatches tuple vs
container on the receiver's resolved type family (a `TupleType` receiver keeps
`std::get<N>`, its index a synthesized `THIRLiteral`). New: `_container_scalar_read` (the
type predicate), `_container_subscript_value_read` (the read gate in `_expr_eligible`), and
container-param admission in `_f1_param_eligible`. **Slice boundary:** value-scalar element
+ fixed-int index/key only -- a runtime-BigInt index/key (the corpus-default `int`) is out
of the scalar slice (its `.to_fixed_check<int32_t>()` narrow is canonically a `THIRCoerce`
on the index, a later cell), and a view-typed (str/bytes) key needs static-storage literal
handling (also later). The `bounds_safe` emit branch is present + faithful but not yet
reached by a routed body (its only producer -- `for i in range(len(c))` -- rides the
for-loop statement-shape cell), so a direct emit unit test covers it. Modest routing (the
corpus's `list[Int32]`/`dict[Int32,...]` functions almost all also loop / `.append` / `len`
/ generate -- none in the slice; 5106 -> 5107 bodies on the existing corpus + a focused
`tests/cases/list/subscript_scalar_read` case routing 4 more, for 5111 bodies / 1229 cases
total), byte-identical over the whole corpus. **Deferred (next container cells):** BigInt / view-typed keys+indices, `dict[K,Any]`
(`any_cast_or_panic` wrap), record/Optional/container *element* results (borrow form),
narrowed-`Optional` receivers, non-name receivers, container-literal-init locals, and the
whole write side (`c[i] = v`, `+=`, `del`, slices).

**Increment 30 opens container iteration** -- `for x in <NativeIterable>:` (list / set /
dict / Span / Array) over a **value-scalar** element (the begin/end loop `auto& __obj_N =
c; auto __beg_N = c.begin(); ...; <elem> var = *__beg_N;`, a new `THIRForEach` node
mirroring `_gen_begin_end_loop`; the value-form loop-var binding reuses the shared
`loop_var_binding`), plus the `len(c)` builtin (`::tpy::__len__(c)`, a `native_name` field
on `THIRCall` so the dispatch keys on the resolved symbol, not the source name -- a user
`len` stays a plain call). `len` as a range bound routes `for i in range(len(c)):`, which
lights up the (previously unreachable) bounds-safe container-subscript branch. **Key
finding:** completing the loop *shape* is a large step, but the routing gain is small (5120
-> 5128 bodies / 1233 -> 1237 cases) because the loop *bodies* overwhelmingly use
constructs still on the AST path -- `print` and container method calls like `.append`
appear in 371 of 393 corpus for-loop files. So the routing bottleneck is now the common
**body constructs** (`print`, `.append`/method calls, the `Int32(0)` constructor-init),
not the statement shapes; those are the next high-leverage unblocks. **Deferred:**
record/non-scalar-element loops (`auto&&`/`const auto&` borrow loop var -- the immediate
next iteration cell), `set`/`Span`/`Array` containers (params not yet admitted),
str/bytes-key dicts, `dict.items()`/tuple-unpack, non-name iterables (only a bare declared
container name routes; a field/subscript/call/literal receiver rides a later cell),
generators & user iterators (the `__iter__`/`__next__` fallback), hoisted loop vars
(post-loop use), `for/else`, consuming/enum iteration.

**Increment 31 completes the for-each family with record elements** -- `for x in
<list[record]>:` where the element is an F1-record. The loop var binds as a borrow alias
(`auto&& x = *__beg_N;`, or `const auto& x` when read-only) and its field reads/writes are
`.field` -- the exact representation a record *param* uses (in `declared`, not `pointers`),
so the loop var flows through the body constructs identically and needs no new emit path:
`loop_var_binding` already produces the record binding, and the only new bytes come from
threading sema's `const_loop_var` through `THIRForEach` (inert for a cheap value scalar --
the typed copy drops const either way -- but load-bearing for a record). A new
`_container_record_iter` predicate admits `list[record]` params (the iteration counterpart
to `_container_scalar_read`, which is subscript-read-only and scalar-valued). **No
rebinding guard is needed:** sema's `_check_nonvalue_rebinding` makes reassigning a
non-value loop var a hard error, so an eligible record loop var is only ever read or
field-mutated through the alias -- both matching Python's reference semantics (a mutation
through the loop var is visible in the list, as in CPython). Routing 5128 -> 5176 bodies /
1237 -> 1241 cases (+48 -- record-iterating functions are common, a larger gain than the
scalar shape cells since admitting the record-list param unblocks whole functions).
**Deferred:** `dict[int, record]` key iteration (the param isn't admitted -- its value read
is a record borrow, out of the scalar-read slice), `set`/`Span`/`Array[record]` (params not
admitted), `dict.items()`/tuple-unpack over record values, non-name iterables, generators &
user iterators, hoisted loop vars.

**Increment 32 opens the expression-statement shape (print + bare calls)** -- `_stmt_eligible`
had no `TpyExprStmt` branch, so THIR routed *zero* expression statements; print (the dominant
one, in 78% of files) blocked whole functions. This cell routes `print(<args>)` for the
no-kwargs common-arg subset (str literal / fixed-int / bool / double -> a new `THIRPrint`
mirroring `gen_print`'s `std::cout << a0 << " " << a1 << ... << "\n";`, each arg tagged with a
`PrintForm` at lowering) plus bare same-module free-function call statements (`foo(x)`
discarded for side effects -> `THIRExprStmt`, reusing `_call_eligible` with a `stmt_position`
flag that admits a `void` return). A minimal `THIRStrLiteral` carries literal args (emitted
via `cpp_string_literal_expr`). Routing 5176 -> **5629 bodies / 1241 -> 1334 cases (+453 --
the biggest single-cell gain)**: print gated a huge tail of small functions (main + helpers),
so unblocking it routes them whole, even though only ~10-15% of print *calls* have all-eligible
args. **Two pre-existing THIR gaps surfaced (print made the functions routable) and were fixed
in the same cell:** (1) `_call_eligible` admitted non-`DEFAULT`-linkage callees, but `@native`
/ `@export(binding="C")` calls are `::`-qualified / raw-symbol at emit -- gated on
`fi.linkage == FunctionLinkage.DEFAULT` (matching the existing `_function_eligible` gate); (2)
a desugar that expands one source line to several statements (tuple-unpack) shares one source
comment via the AST's `no_source_comment` flag -- now carried onto the THIR stmt base and
honored by `_emit_stmts`. **Deferred:** `str`/`BigInt`/f-string/container/tuple/Optional/record/
`None` print args (ride the str/BigInt/form rungs), all print kwargs (`sep=`/`end=`/`file=`/
`flush=`), and container method-calls (`.append` -- needs method-call-site lowering, a separate
cell).

**Increment 33 opens method-call sites (container mutation/read calls)** -- no method call
routed before this cell (only `super().__init__` inside ctors, which is structural, not a call
site). A new `THIRMethodCall` (receiver + args + the facts `gen_call_from_fi` dispatches on,
materialized at lowering) mirrors `_gen_method_call`'s pass-through subset across all three
emit arms: `@cpp_template` expansion (`xs.sort()` -> `std::stable_sort(xs.begin(), xs.end())`),
`@native(..., function=True)` free functions with the receiver prepended (`xs.pop()` ->
`::tpy::pop_back(xs)`), and plain/@native-renamed members (`xs.append(v)` -> `xs.push_back(v)`,
`xs.clear()`). The gate (`_method_call_eligible`) admits a bare-name receiver in the already-
admitted `list[scalar]` / `dict[fixed-int, scalar]` param family (never a pointer-local -- no
deref/arrow/narrowing) with value-scalar args into scalar / `Own[scalar]` / `readonly[scalar]`
slots (the `gen_call_arg` inline-template pass-through: scalars copy bare, no move/lift/temp),
a scalar result in value position (`a = xs.pop()`) or a discarded void/scalar in statement
position, and rejects every special-emit marker (static / super / module-qualified / typed-dict
/ consuming / `{cpp}` template / `cpp_return_type` cast / `@error_return` / LiteralType-mangled
overloads). Routing 5629 -> **5640 bodies / 1334 -> 1340 cases (+11 on the merged baseline)**:
the shape is covered, but most corpus `.append` sites sit on container-*literal locals*
(`xs = [...]`), whose var-decls don't route -- container-literal-init locals are now the
routing co-blocker and the next high-leverage cell. **Deferred:** `set`/`Span`/`Array`
receivers (params not admitted), str/bytes args (owned-copy conversion), record /
`Own[record]` args (ownership boundary), non-name receivers (`self.items.append(...)`),
user-record method receivers and same-module `self.helper()` call sites (a different, bigger
frontier: the record emit path with temps/TypeParamRef handling), consuming receivers,
generic `inferred_type_args` methods, negative-literal args (unary minus, a pre-existing
expression-slice boundary).

**Increment 34 opens container-literal locals** -- `xs = [1, 2]` / `xs: list[T] = []` /
`d = {k: v}` / `s = {a, b}` var-decls route via a new `THIRContainerLiteral` (emit dispatched
on the sema-RESOLVED container family: sema's PendingListType resolution decides
vector-vs-array before lowering, so a mutated literal emits `std::vector<T> xs = {..}` and a
read-only one `std::array<T, N> ys = {..}`; dict/set spell their runtime ctor
`::tpy::ordered_map<K, V>({{k, v}, ..})` / `ordered_set<T>({..})`, empty forms `()` /
`std::vector<T>{}`). The local enters `declared` with its resolved type, so every receiver
shape already routed for container params (method calls, subscript reads, `len`, iteration)
lights up on locals; `_container_scalar_read` widened to `Array[scalar, N]` and the `len` gate
to `is_array` (probe-verified identical emits). **Two latent type-plumbing classes surfaced
and fixed:** (1) a literal-seeded local's *use sites* keep the pre-resolution
`PendingListType` on the expr -- receiver gates now read the declared binding type (mirroring
`_is_len_call`) and `_lower_expr` resolves pending containers via sema's per-literal record,
honoring the fully-resolved-type invariant; (2) IntLiteral element types leak into the
resolved method fi's slots, `pop`/subscript results, loop `elem_type` (whose `to_cpp()` emits
the VALUE), and print/binop facts -- a shared `_resolved_scalar` (unwrap + default-int
resolution) is applied at those checks, and the for-each/print lowerings resolve the emitted
types. **Two aliasing/paren gates:** a REASSIGNED container-literal local is a pointer-local
on the AST path (`a = b` rebinds the alias; a plain value decl would silently copy) --
rejected via `prescan.reassigned`; a fixed-target binop whose operands are BOTH
IntLiteral-typed non-names takes the AST's no-paren-wrap literal-operand branch
(position-dependent) -- rejected in `_binop_eligible`. Routing 5640 -> **5678 bodies /
1340 -> 1354 cases**. **Deferred:** container locals as call args (the visible next
co-blocker -- `f(xs)` keeps `main()`-shaped callers on AST), `[0] * n` (`TpyListRepeat`),
nested container literals, str/record/Optional elements, empty-`Array` literals, container
reassignment/aliasing, subscript writes, tuple locals, literal-operand binops (the no-paren
branch).

**Increment 35 widens call args to pass-through containers** -- `use_list(xs)` /
`wipe(d)` / a literal local passed onward now route: a bare-name arg with a builtin-container
binding into a NON-Own concrete builtin-container param emits as the bare name on both paths
(`gen_call_arg`'s ownership cascade never fires for that slot shape), so
`_container_pass_through_arg` widens the arg checks in `_call_eligible` and
`_method_call_eligible` -- no new node or emit. `Own[container]` slots (auto-move
`f(std::move(xs))`), `Span` slots (`::tpy::as_mut_span(xs)`), and protocol slots
(`Iterable`, e.g. `xs.extend(ys)`) stay on the AST path, each pinned by an ineligibility
unit. First cell under the leaner per-cell test convention (scaffold units only -- see the
ledger): the shape is abundant in the corpus, so no dedicated corpus case. Routing 5678 ->
**5689 bodies / 1354 -> 1356 cases**.

**Increment 36 opens logical `and`/`or`/`not` + inline chained comparisons** -- the
bool condition/value slice. Bool-result `&&`/`||` over bool operands lower to the existing
`THIRBinOp` (sema leaves `resolved_binop` None, so the bare-operator arm already renders
`(l && r)`); `not <bool>` gets the one new node, `THIRUnaryNot` (`(!(operand))`); a
chained comparison whose intermediates are all `_is_simple_expr` (the predicate imported
from `ExpressionGenerator`, not mirrored, so the inline-vs-statement-expr trigger cannot
drift) lowers to a left-fold of its sema pairs with bare `&&` (`((a < b) && (b < c))`).
For every admitted bool shape `gen_truthy_expr` reduces to the value render, so conditions
reuse `_emit_expr` -- no position tracking. Rejected: non-bool results (Python value
semantics -> `_gen_logical_value` temp+ternary), non-bool operands (truthiness wraps),
non-simple chain intermediates (the GCC statement-expr `_cmp` temps). The byte-diff exposed
a latent compare gap the new `not` arm reached: the rb=None derived-comparison arm admitted
record operands (`@total_ordering`'s `not (self <= other)`, where the AST derefs `(*this)`)
-- compare operands are now pinned to resolved scalars. Routing 5689 -> **8124 bodies /
1356 -> 1360 cases** (+2435, the largest single-cell gain to date -- conditions gate
everything).
**Increment 37 opens scalar type-constructor calls** -- `Int32(0)` / `UInt32(x)` /
`Int64(a + b)` / `Float64(1.5)` / `bool(n)` / zero-arg `Int32()` route in every expression
position the slice covers (var-decl init, call/method-call arg, binop operand, print arg,
return value, field write, ctor MIL source, container-literal element). Sema resolves these
to a `@cpp_template` `__init__` overload whose stored template is already fully substituted
(`_resolve_cpp_template_type_params` bakes `{cpp}`/class type params; `_check_cast_safe`'s
`static_cast` rewrite lands on the same node fact), so the AST emit reduces to
`expand_cpp_template(template, None, *args)` -- mirrored by a `cpp_template` field on
`THIRCall`, expanded at emit with no receiver. The gate (`_scalar_ctor_call_eligible`)
requires a positional-only template (`{0}`, `{1}`, ...; any surviving named placeholder
keeps the call on the AST path), an eligible-scalar result, and eligible-scalar args into
scalar / `Own[scalar]` / method-type-param slots (`_ctor_arg_slot_ok` peels the stub's
`Ref(TypeParamRef)` generic slot, where `gen_call_arg`'s target hint is inert). Emit arms
mirrored: same-type/literal passthrough `{0}`, generic `::tpy::int_cast_check<intN_t>({0})`
(or its safe-cast `static_cast` rewrite), `static_cast<double>({0})` int->float,
`({0} != 0)` bool, `0` zero-arg. Rejected by design (AST path): str/bytes/BigInt/Float32/
Char conversions (fail the scalar checks; `float("nan")`'s constexpr fold stays AST via its
str-literal arg), enum ctors (`enum_from_value`), borrowing-view ctors (`call_type`),
literals outside int32 range (the `static_cast` wrap `_gen_int_literal_value` adds), and
unary-minus args (the pre-existing expression-slice boundary). Routing 5689 ->
**5783 bodies / 1356 -> 1393 cases**.

**Increment 38 opens the F6 str slice: str values (S1)** -- str/StrView params,
literal-init locals (the `PendingStrType` view/owned resolution is sema-final
pre-lowering, read through `ViewVarInfo` like `_resolve_view_storage`), print args
(raw stream), comparisons (resolved dunder templates / bare derived operators),
`len(s)`, owned-str returns, and same-type call args. The view->owned copy at a
decl-init/return sink is an explicit `THIRFormConvert` (str BORROW -> STORAGE,
`std::string(x)`, the `_view_source_to_owned` chokepoint), driven by a load-bearing
str name-form tag (string_view param / view local = BORROW, owned local = STORAGE,
literal = VALUE and never wrapped). Rejected: str aug-assign/concat (S3), cross-type
str-family coercions (position-dependent), reassigned str params (owned-copy
prologue), multi-overload callees with str-literal args (`_wants_str_literal_pin`),
`String`/`Char`/`Literal[str]` bindings, f-strings (S2), bytes (S6). Also closed a
latent overload gap the cell unmasked: an overload IMPL body is emitted once per stub
with per-stub dead-branch facts, but gen_body's THIR interception keys on id(func) --
any callable in a multi-entry overload set is now rejected. Routing 5689 ->
**19613 bodies / 1356 -> 3271 cases** (str shapes pervade the implicitly-compiled
stdlib modules, so nearly every case now routes bodies).

Increments 36-38 were developed as parallel cells (separate worktrees, each
byte-diff-gated solo) and integrated sequentially; the integration reconciled the
incr-36 compare-operand scalar pin with the S1 str slice (widened to
scalar-pairs-or-str-pairs) and re-ran the combined whole-corpus byte-diff green:
the integrated tally is **22165 bodies / 3271 cases** (vs 5689 / 1356 at incr 35).

---

## Motivation

### The Problem

TPy's compiler currently uses a single representation: the AST from `parse/nodes.py`,
mutated in-place by sema with 50-70 optional annotation fields (`resolved_function_info`,
`inferred_type_args`, `bounds_safe`, `ptr_non_null`, etc.). Codegen reads the annotated
AST plus sema side tables (`expr_types`, `var_types`, fact dicts) via a direct reference
to the `SemanticAnalyzer`.

This works, but creates three concrete problems:

1. **Tight coupling.** Codegen cannot run without a live sema instance. Sema state is
   spread across AST node fields, `SemanticContext` dicts keyed by `id()`, and
   `BorrowTracker` string maps. There is no self-contained "this is what sema produced"
   artifact.

2. **Hard to debug.** There is no way to dump the fully-typed, fully-resolved program
   state between sema and codegen. Debugging requires mentally reconstructing what sema
   wrote into each node and side table.

3. **Borrow checking precision.** The current borrow checker operates on the AST with
   `freeze()`/`restore_from_frozen()` at branch points via `FlowFacts`, but merges
   borrow states conservatively at join points (union of borrows from both branches).
   This means a borrow active in either branch is assumed active in both -- so a move
   on one path can conflict with a borrow on a mutually exclusive path. The tracker
   also uses string-based storage keys with limited field-path support (`"self.items"`),
   and cannot split borrows by independent fields.
   A CFG-based IR is the standard solution for path-sensitive safety analysis.

4. **Agent boundary.** When LLM agents work on the compiler, the lack of clean phase
   boundaries makes it easy to accidentally couple new code to sema internals. A well-
   defined IR contract between phases prevents this class of errors.

### Why Two IRs

One IR is not enough because sema and borrow checking have different needs:

- **Type checking and overload resolution** work naturally on trees. Expressions have
  types, calls resolve to specific functions, generics are instantiated. A tree-shaped IR
  is the right fit.

- **Borrow checking, liveness, and move optimization** need path-sensitive analysis:
  "is this variable live on *every* path reaching this point?" This requires a CFG where
  each basic block has explicit predecessors and successors, and dataflow facts propagate
  along edges.

Trying to do both on the same representation forces either a tree that carries CFG
information (awkward) or a CFG that carries type-checking state (wasteful). Two IRs
let each phase use the right structure.

### What THIR/MIR unblocks (running ledger)

A growing list of concrete defects and duplication whose *clean* fix is gated on the
IR migration -- maintained so the migration's priority can be judged against accumulated
cost rather than asserted. Add entries here as they surface; cite the BUGS.md / TODO.md
source. Some entries are closeable pre-IR only as a *rejection* (loud diagnostic), not a
*fix*; those are the strongest signal, because the feature genuinely cannot be expressed
in the current model.

- **Borrow-form vs storage-form (Open Questions item 9 -- the largest cluster).** Tuple
  (and Optional/Union) C++ form is reconstructed per-site in codegen instead of being a
  type fact, so every new boundary shape needs another consumer-side dispatch patch:
  - *Tuple local with a durable reference member silently copies it at yield/return* --
    was **[MED, silent CPython divergence]** (TPy `5` vs CPython `99`). **FIXED pre-IR**
    by the tuple borrow-pointer unification (`unify-tuple-borrow-pointer-form`,
    `docs/TUPLE_BORROW_UNIFICATION_PLAN.md`): the bound local is a pointer-form tuple
    (`std::tuple<int, Box*>`), constructible and rebindable where a reference field is
    not, and it aliases correctly across suspensions -- disproving this item's earlier
    "THIR-gated" claim for the durable-share case. The cost was the consumer-side
    dispatch inventory now listed under Open Questions item 9, plus three adversarial
    audit waves closing provenance escapes -- the per-shape fact-propagation burden item
    11 is about.
  - Nested tuple where outer/inner forms disagree -- **[MED]** (BUGS.md).
  - Rvalue tuple-of-records into a ref/pointer-form slot -- **[MED/LOW]** (BUGS.md).
  - Generic `V | None` instantiated with `V = Ptr[T]` (double-pointer) (BUGS.md).
  - Bare-Optional yield missing the storage->pointer bridge (BUGS.md).
  - Union `match` capture: value-variant storage-form binding vs pointer-variant
    borrow subject (also an undesigned-aliasing-form design question) (BUGS.md).
  - View-family `*args` elements (str/bytes) reconstructed per-site as storage form
    (`varargs<std::string>` / `varargs<std::vector<uint8_t>>`) instead of the borrow
    form the scalar param already uses (`string_view` / `span<const uint8_t>`), so every
    individual arg is copied into the owned pack (TODO.md). Intentional + memory-safe
    today; the zero-copy borrow form is all-paths-or-nothing (one element type, so every
    consumer must agree) and rides on this item's general element-as-borrow +
    materialize-at-owned-sink rule rather than being bespoke work. The inventory the MIR
    rule must cover, from a per-shape probe: (1) pack construction -- individual args
    zero-copy, `*container` star-unpack needs a `vector<view>` materialization (the list's
    `std::string`s aren't contiguous views), varargs->varargs forwarding already fine;
    (2) every owned sink -- return, var-init, container insert, dict key, tuple element,
    comprehension element, `list(parts)` -- each currently re-decides whether to wrap;
    (3) the per-site element-type derivations that already *disagree* today (statement-`for`
    binds `string_view`, comprehension binds `const std::string&`, slice-result local binds
    `varargs<std::string>`) -- unifying these is the bulk of the consumer-side dispatch;
    (4) the lifetime half -- generator/coro frame capture must OWN a copy (captured views
    dangle past the call statement for non-literal args; async `*args` is unsupported today,
    so only the simple-peephole lambda and resumable-struct frames apply). Read-only uses
    (len, print, concat, element-into-str-param, statement-`for` iteration) are already
    correct. Probed + Codex-co-validated all-paths-or-nothing 2026-06.
  Several smaller cases in this class *were* closed pre-IR by extending consumer-side
  predicates -- but each one touched another dispatch site, which is exactly the cost the
  IR fact removes. The same fact also dissolves the sema `Ref[T]` wrapper, which today
  co-exists with codegen's positional re-derivation as a second borrow-form oracle
  (Open Questions item 12).
- **Path-insensitive borrow checking (Motivation problem 3).** The AST borrow checker
  merges borrow states conservatively at join points (union over branches), so a move on
  one path conflicts with a borrow on a mutually-exclusive path -- false positives a
  CFG-based MIR resolves.
- **Extent-scoped loans for match-arm bindings (BUGS.md [HIGH]).** A non-scalar `match`
  arm binding is an `auto&` borrow into the subject's storage; mutating the subject root
  within the arm (a method that reassigns it, or an alias) dangles it -- silent UB,
  verified. It cannot be fixed soundly today: the whole-function mutation facts the
  deferred-check resolver reads are extent-blind, so they cannot express "the subject root
  was mutated *while this arm's binding was live*", and copying the binding is off the
  table (str/BigInt perf, view dangle, reference-type CPython-aliasing divergence). The
  scalar half is closed by copying free-copy scalars; the non-scalar half wants a MIR
  `Place(subject-root)` + `LoanInfo(arm extent)` loan that rejects root mutation while the
  loan lives -- a concrete motivator for the place/loan model, not just a precision win.
- **Hand-copied sema/codegen predicate mirrors.** Predicates duplicated across phases and
  kept in lockstep only by discipline: `directly_implements_dynamic` (sema mirror of
  codegen, now 4 call sites -- BUGS.md), the default-ctor predicate and the param-const
  verdict (TODO.md). A shared-IR contract removes the duplication class.
- **Eager per-shape local binding decisions (Open Questions item 11).** Non-value and
  pointer-repr-tuple locals pick their C++ shape eagerly at the binding site across ~7
  parallel mechanisms (ref binds, pointer-locals + rvalue slots, optional-locals,
  frame_slot fields, borrow-/storage-form tuple sets), each with its own
  init-deferral/rebind/alias rules -- the source of the optional brace-init corruption
  class, the tuple owning/alias rebind rejection (BUGS.md), and the per-shape
  provenance-fact propagation that three adversarial audit waves patched escape-by-escape.
  MIR's place/loan model with late representation selection + a mem2reg-style fold
  replaces all of it.
- **Generic str/bytes ABI perf split (Open Questions item 8).** **[perf, not
  correctness]** generic-`T`-over-`str` materializes `std::string` at each call site.
  Documented; low priority.
- **Move/relocation safety of inline storage (MIR move/copy lowering).**
  `UninitArrayStorage`'s move ctor `memcpy`d its element array unconditionally,
  silently corrupting a non-trivially-relocatable element: an SSO `std::string`'s
  data pointer aliases its own inline buffer, so a byte-copy leaves the moved-to
  string pointing into the moved-from (soon-dead) storage. It surfaced as a
  `stack-use-after-return` for `async def -> str` (the result flows through a
  moved `Poll<std::string>`), ASan/hardened-allocator-only and benign on glibc --
  the worst kind of latent miscompile. The first fix (a per-storage liveness
  bitset + element-wise move) was rejected: liveness is the OWNER's state (a
  size, a head/count, a flag), so duplicating it in the storage is redundant and
  over-general (a ring buffer's liveness is not even a prefix). The shipped
  design instead keeps the storage dumb -- `memcpy` move for trivially-copyable
  `T`, the move **deleted** for non-trivial `T` (silent corruption becomes a
  compile error) -- and introduces `tpy::UninitStorage<T>` for single-optional-value
  owners (Poll/Rc-payload/channel-send), whose one liveness bit IS the owner's
  (no duplication). The per-type "trivially relocatable?" decision (here a
  conservative `is_trivially_copyable` proxy) and the storage-vs-slot choice are
  exactly what MIR's move/copy lowering should own and verify centrally, rather
  than each hand-written container re-deriving it and one (the old move ctor)
  getting it wrong.
- **Joint generic inference: a pending-typed arg co-resolved by a sibling argument.**
  **[ergonomics, not correctness]** An untyped empty-container local (`heap = []` ->
  `PendingList[???]`) passed to a generic free function alongside an argument that fixes
  the type parameter is not resolved: for `heappush(heap, Entry(copy(src[i])))` against
  `heappush[X](heap: list[X], item: Own[X])`, `X = Entry[T]` is inferable from `item`, but
  `match_type_with_inference` is directional (param <- one arg at a time) with no shared
  unification variable tying `heap`'s pending element to `X`, so the local stays
  `PendingList[???]` and the call is rejected. Forward-from-usage deduction already covers
  the method-call shape (`xs.append(5)`) and the concrete expected-type shape (`f(x)` where
  `f` wants `Container[Int32]`) -- see `BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md` Phase 3a --
  but the joint case (co-resolve a pending arg with a type param determined by a *sibling*
  arg, then write the result back onto the local) is the HM-style constraint-solving step
  that doc defers to "Phase 3+". Natural on MIR's unification-variable model; awkward to
  bolt onto the directional AST matcher. Workaround: annotate the local
  (`heap: list[Entry[T]] = []`). Surfaced reviewing the owned-storage-form inference fix.
- **Simple-generator peephole eager-body divergence (BUGS.md "runs post-yield code BEFORE
  delivering" / "runs the body prologue eagerly").** **[MED, silent ordering divergence]**
  The single-yield lambda peephole runs a prologue at construction and post-yield code one
  pull early instead of suspending. The fix -- route such generators to the resumable path --
  is correct but IR-entangled, so it rides the migration: (1) rerouting some shapes hits the
  resumable path's own IR-gated gaps (the borrow-form `tuple<int,Box*>` vs `tuple<int,Box>`
  yield, Open-Q item 9; default-args-on-resumable-factory), so a broad reroute regresses
  previously-building cases; (2) the only pre-IR alternative -- a *syntactic* "observable
  prologue/post-yield" predicate to reroute selectively -- is a semantic-purity problem that
  leaks (a denylist keeping local bindings mis-times `x = f()`/`x = xs[i]`/`x = global`; an
  allowlist of pure-arith counters reroutes `i = Int32(0)` back into the tuple bug). Once MIR
  makes representation selection late and the resumable path's borrow-form gaps dissolve, the
  reroute becomes unconditional and complete. (Investigated + abandoned pre-IR 2026-06-22,
  Codex-validated.)

---

## Prior Art

| Compiler | IRs | Notes |
|----------|-----|-------|
| Rust (rustc) | HIR -> MIR -> LLVM IR | MIR is where borrow checking, move analysis, and optimizations happen. Pre-monomorphization. |
| Swift (SIL) | AST -> raw SIL -> canonical SIL -> LLVM IR | SIL carries ownership and lifetime information. Two forms (raw/canonical) separate verification from optimization. |
| Go | AST -> SSA | Single IR, SSA-based. No borrow checking needed (GC). |
| C++ (Clang) | AST -> LLVM IR | No intermediate -- AST is heavily annotated, similar to TPy's current state. |

TPy's situation is closest to early Rust before MIR was introduced (2016). Rust had the
same problem: borrow checking on the tree-shaped HIR was imprecise and generated false
positives. MIR solved this.

---

## THIR Design

### Goal

Produce an **immutable, self-contained** representation of a fully-analyzed module that
codegen (and later MIR lowering) can consume without referencing the `SemanticAnalyzer`.

### What Changes

| Today | After THIR |
|-------|------------|
| AST nodes have 50-70 optional annotation fields, mostly `None` after parsing | THIR nodes have required fields -- all types resolved, all overloads bound |
| `expr_types[id(node)]` side table | `THIRExpr.result_type: TpyType` on the node |
| `var_types[id(node)]` side table | `THIRVarDecl.resolved_type: TpyType` on the node |
| `ptr_deref_facts[(line, key)]` dict | `THIRDeref.non_null: bool` on the node |
| `subscript_bounds_facts` dict | `THIRSubscript.bounds_safe: bool` on the node |
| `all_last_uses: set[int]` + `movable_locals: set[str]` | `THIRName.is_last_use: bool` + `THIRName.is_movable: bool` on the node |
| `resolved_function_info` optional field | `THIRCall.target: ResolvedFunction` required field |
| Per-function analyzer dicts (`function_scan_results`, `function_hoisted_vars`, `function_movable_locals`, `function_move_through_vars`, `function_global_decls`) | `THIRFunction.layout` and `THIRFunction.declared_globals` |
| Module options from sema/context (`default_int_type`, `default_int_for_literal`) | `THIRModule` required fields |
| View/literal registries (`str_vars`, `bytes_vars`, `list_literals`, `dict_literals`, `set_literals`) | explicit `view_info` / `literal_info` on the relevant THIR nodes |
| Codegen holds `self.ctx.analyzer` reference | Codegen receives `THIRModule`, no analyzer reference |

### THIR Node Hierarchy

The THIR mirrors the AST structure but with all analysis results materialized:

```
THIRModule
  functions: list[THIRFunction]
  records: list[THIRRecord]
  protocols: list[THIRProtocol]
  enums: list[THIREnum]
  globals: list[THIRGlobal]
  top_level: list[THIRStmt]
  default_int_type: TpyType
  default_int_for_literal: TpyType
  type_registry: TypeRegistry          # shared, immutable after sema

THIRFunction
  name: str
  params: list[THIRParam]
  return_type: TpyType
  body: list[THIRStmt]
  layout: THIRFunctionLayout
  declared_globals: frozenset[str]
  mutated_params: frozenset[str]       # from Phase 2 propagation
  is_readonly: bool
  return_borrows_from: frozenset[int]  # param indices
  is_generic: bool
  type_params: list[TypeParam]
  overload_group: str | None
  generator: THIRGeneratorInfo | None

THIRFunctionLayout
  hoisted_locals: frozenset[str]
  movable_locals: frozenset[str]
  move_through_locals: frozenset[str]
  pointer_locals: frozenset[str]
  ref_locals: frozenset[str]
  reassigned_locals: frozenset[str]    # affects C++ declaration style / slot handling

THIRGeneratorInfo
  yield_type: TpyType
  states: list[THIRGeneratorState]
  frame_fields: list[THIRSyntheticField]
  strategy: GeneratorStrategy          # current codegen strategy, if any

GeneratorStrategy
  = current backend-defined enum matching generator lowering variants

THIRGeneratorState
  state_id: int
  resume_label: str

THIRSyntheticField
  name: str
  type: TpyType

THIRParam
  name: str
  type: TpyType                        # fully resolved (Own[T], readonly[T], etc.);
                                       # no Ref[T] -- dissolved into form facts
                                       # (Open Questions item 12)
  default: THIRExpr | None
  is_mutated: bool                     # from mutation analysis
```

#### Expressions

```
THIRExpr (base)
  result_type: TpyType                 # always present
  loc: SourceLocation | None

THIRName
  name: str
  result_type: TpyType
  is_last_use: bool                    # from liveness analysis
  is_movable: bool                     # in movable_locals

THIRCall
  target: ResolvedFunction             # fully resolved -- function, overload index, etc.
  args: list[THIRExpr]
  type_args: tuple[TpyType, ...]       # instantiated generics (empty if non-generic)
  result_type: TpyType

THIRMethodCall
  receiver: THIRExpr
  method: ResolvedFunction
  args: list[THIRExpr]
  type_args: tuple[TpyType, ...]
  deref_depth: int                     # Ptr auto-deref count
  result_type: TpyType

THIRFieldAccess
  receiver: THIRExpr
  field: str
  deref_depth: int
  non_null: bool                       # proven non-null at this deref
  result_type: TpyType

THIRSubscript
  container: THIRExpr
  index: THIRExpr
  bounds_safe: bool                    # proven in-bounds
  result_type: TpyType

THIRBinOp
  left: THIRExpr
  op: BinOpKind
  right: THIRExpr
  resolved: ResolvedBinop | None       # operator overload, if any
  divisor_non_zero: bool
  result_type: TpyType

THIRNamedExpr                            # walrus operator (:=)
  name: str
  value: THIRExpr
  result_type: TpyType

THIRCoerce
  expr: THIRExpr
  from_type: TpyType
  to_type: TpyType
  kind: CoercionKind                   # widening, own-strip, optional-wrap,
                                       # runtime-bigint, etc.

THIRLiteral
  value: int | float | str | bool | bytes | None
  result_type: TpyType

THIRListLiteral
  elements: list[THIRExpr]
  element_type: TpyType                # resolved element type
  literal_info: THIRLiteralInfo | None
  result_type: TpyType

THIRDictLiteral
  items: list[(THIRExpr, THIRExpr)]
  key_type: TpyType
  value_type: TpyType
  literal_info: THIRLiteralInfo | None
  result_type: TpyType

THIRSetLiteral
  elements: list[THIRExpr]
  element_type: TpyType
  literal_info: THIRLiteralInfo | None
  result_type: TpyType

THIRTupleLiteral
  elements: list[THIRExpr]
  result_type: TpyType

THIRTupleUnpack
  targets: list[THIRExpr]
  value: THIRExpr
  result_type: TpyType

THIRComprehension
  kind: AggregateKind
  element: THIRExpr
  clauses: list[THIRComprehensionClause]
  result_type: TpyType

THIRGeneratorExpr
  element: THIRExpr
  clauses: list[THIRComprehensionClause]
  result_type: TpyType

THIRComprehensionClause
  = For(target: THIRExpr, iterable: THIRExpr)
  | If(condition: THIRExpr)

THIRLiteralInfo
  needs_stable_storage: bool

THIRViewInfo
  source_kind: str                     # str / bytes / span / ptr / field / element
  source_expr: THIRExpr

# ... (array, f-string, lambda, etc.)
```

#### Statements

```
THIRVarDecl
  name: str
  resolved_type: TpyType              # always resolved
  init: THIRExpr | None
  is_hoisted: bool                     # escapes inner scope
  is_pointer_local: bool               # T* slot (non-value type local)
  view_info: THIRViewInfo | None
  narrowing_facts: dict[str, TpyType]  # from isinstance/assert on this decl

THIRAssign
  target: THIRExpr                     # name, field, subscript
  value: THIRExpr
  view_info: THIRViewInfo | None

THIRAugAssign
  target: THIRExpr                     # name, field, subscript
  op: BinOpKind                        # Add, Sub, etc.
  value: THIRExpr
  resolved_inplace: ResolvedFunction | None  # __iadd__ etc. overload

THIRDelItem
  target: THIRExpr                     # subscript expression (del obj[key])

THIRForEach
  var: str
  elem_type: TpyType
  iterable: THIRExpr
  body: list[THIRStmt]
  orelse: list[THIRStmt]               # for/else body (runs if no break)
  is_consuming: bool                   # consuming iteration selected
  is_native: bool                      # NativeIterable range-for

THIRWhile
  condition: THIRExpr
  body: list[THIRStmt]
  orelse: list[THIRStmt]               # while/else body
  narrowing_facts: dict[str, TpyType]  # condition narrowing in body

THIRIf
  condition: THIRExpr
  then_body: list[THIRStmt]
  else_body: list[THIRStmt]
  then_narrowing: dict[str, TpyType]   # type narrowing in then-branch
  else_narrowing: dict[str, TpyType]

THIRAssert
  condition: THIRExpr
  message: THIRExpr | None
  narrowing_facts: dict[str, TpyType]  # narrowing after assert passes

THIRMatch
  subject: THIRExpr
  arms: list[THIRMatchArm]

THIRMatchArm
  pattern: THIRPattern
  guard: THIRExpr | None
  body: list[THIRStmt]
  narrowing_facts: dict[str, TpyType]  # type facts for this arm

THIRReturn
  value: THIRExpr | None
  is_dangling: bool                    # if True, sema already reported error

THIRYield
  value: THIRExpr | None
  state_id: int                        # generator state machine ID

THIRTryExcept                          # @error_return(E) zero-cost error handling
  kind: TryKind                        # ErrorReturn or Throw
  error_local: str | None
  body: list[THIRStmt]
  handlers: list[THIRExceptHandler]

TryKind
  = ErrorReturn
  | Throw

THIRWith
  context: THIRExpr
  var: str | None
  body: list[THIRStmt]
```

### Lowering Pass: AST + Sema -> THIR

A new pass (`tpyc/thir/lower.py`) walks the annotated AST and sema side tables,
producing THIR nodes:

```python
def lower_module(ast: TpyModule, analyzer: SemanticAnalyzer) -> THIRModule:
    """Convert annotated AST + sema state into a self-contained THIR."""
    ...
```

This is where all `id()`-keyed lookups, optional field reads, and side table accesses
are resolved into concrete THIR fields. After lowering, the analyzer can be discarded.

Two requirements are important here:

1. **Implicit coercions must be materialized.** Every sema-selected conversion becomes
   an explicit `THIRCoerce` at the exact site where it applies: call arguments,
   assignments, returns, operator operands, literal elements, default arguments,
   `Own` stripping, optional wrapping, enum-from-value, `str -> StrView`,
   `bytes -> BytesView`, and the other coercion families currently scattered across
   sema. THIR lowering must not rely on codegen or MIR lowering to rediscover them.

2. **THIR must be codegen-complete.** If current codegen needs per-function layout
   facts, view provenance, literal lowering metadata, module integer defaults,
   generator frame shape, or declared globals, THIR must carry an explicit equivalent.
   The shape may improve over today's analyzer dicts, but the analyzer dependency must
   end after THIR lowering.

### Debugging: `--dump-thir`

A human-readable text format for inspecting the THIR:

```
fn main() -> Void:
  %items: list[Int32] = list_literal([1, 2, 3])    # elem_type=Int32
  %total: Int32 = int_literal(0)
  for %x: Int32 in %items [consuming=false, native=false]:
    %total = binop(%total, Add, %x)                 # resolved=Int32.__add__
  call print(%total)                                 # target=builtins.print
```

This makes the resolved types, overloads, and optimization facts visible at a glance.

### Form as a First-Class THIR Fact (resolved 2026-06)

Resolves Open Questions 9 (tuple/form fact), 11 (uniform local model -- the THIR
half), and 12 (RefType fate). The ground-truth surface this must subsume is
`docs/THIR_FORM_INVENTORY.md`; that document is the binding checklist (completion
= every item closed + the AST form-codegen retired). A spike validated the node
mechanism against the live `convert()` chokepoint (24/24 across optional/union/
tuple x both directions x const x move) and reproduced the minimal field-read
slice (`const Inner* x = ::tpy::optional_to_ptr(b.inner)`) byte-for-byte.

#### Framing constraint: byte-identical, for validatability (not churn)

The form slice must emit C++ byte-identical to the AST path (verified by forcing
`--thir-codegen` on and diffing -- zero snapshot diffs, the same gate increments
1-5 pass). The reason is not snapshot-churn cost; it is that a zero-diff is the
*only* way a human can confirm thousands of cases still compile correctly. If a
form change rewrote thousands of snapshots, no reviewer could validate them. So
byte-identity is the migration's correctness proof during AST/THIR coexistence.
Representation *normalization* (collapsing the `LocalCppForm` zoo) is real and
desirable, but it belongs to MIR's late-representation fold (Open Q 11's MIR
half), where it is the explicit goal -- not smuggled into THIR where it would
forfeit the zero-diff net.

#### Two facts, not one: value-form vs local-representation

A var-decl like `x = b.field` braids two orthogonal facts that the design keeps
separate:

1. **Value form** (Open Q 9) -- the borrow-vs-storage axis of a *value*:
   `BORROW` (`T*`, `T&`, `variant<A*,B*>`, `tuple<...,T*>`, `optional<T>` read as
   `T*`) vs `STORAGE` (`T`, `optional<T>`, `variant<A,B>`, `tuple<...,optional<T>>`)
   vs `VALUE` (value types -- the two forms coincide). This is the `CppForm` enum
   lifted from a codegen-local notion to a carried IR fact. It is SEMANTIC: it
   survives into MIR (it is about ownership/aliasing).

2. **Local representation** (Open Q 11) -- the C++ *slot shape within a form*:
   `T&` alias vs `T*`+rebind-slot vs `optional<T>` deferred-init vs `frame_slot<T>`
   vs pointer-element tuple. This is the existing `LocalCppForm` (9 variants) +
   the 22 side-sets. It is COMPATIBILITY metadata -- carried only to reproduce
   today's eager C++ byte-identically; MIR's fold subsumes ONLY this level.

These co-arrive (the first conversion-bearing slice needs both), so THIR carries
both from increment 1. Only the MIR fold defers.

#### The form tag lives on the expression, not the type

The same `TpyType` (`Inner | None`) renders as `Inner*` (borrow) or
`std::optional<Inner>` (storage) depending purely on POSITION -- so form is a
positional fact, not intrinsic to the type. Putting a form tag on the type would
force two non-canonical type instances per tuple/optional; putting it on the expr
keeps types canonical and matches today's `FormValue.form` (the producing emitter
records the form it actually emitted). So:

```
THIRExpr (base)
  result_type: TpyType
  form: Form               # NEW: BORROW | STORAGE | VALUE  (default VALUE)
  loc: SourceLocation | None
```

`Form.VALUE` default leaves the existing value-scalar slice untouched (every
current node is VALUE). `result_type` still selects the conversion FAMILY; `form`
says which side of the axis. Form is SET by lowering through a single classifier
(one writer -> no divergence); the coerce boundary ASSERTS rather than silently
passing a value-form expr through, so a `VALUE` tag can never mask a *missed*
conversion. This positional placement is also what resolves the consumer-dictated
exhibits (Open Q 9's `key=` lambda, async/await union, match capture): the
conversion is inserted at each CONSUMER site, so one definition with one param
form is bridged independently by each consumer -- no definition-site guess.

`THIRVarDecl` additionally carries the local-representation fact:

```
THIRVarDecl
  ...
  form: Form                       # coarse, SEMANTIC -- drives the insertion rule
                                   #   (a local is NOT uniformly BORROW: storage-
                                   #   optional / storage-tuple loop/unpack vars
                                   #   are STORAGE form)
  cpp_local_representation: ...    # the LocalCppForm analog, carried VERBATIM.
                                   #   COMPATIBILITY metadata: explicitly
                                   #   non-semantic, FORBIDDEN for any other THIR
                                   #   node to depend on; MIR's fold subsumes only
                                   #   this. Do not redesign it here (that is
                                   #   divergence risk + MIR's job).
```

#### THIRFormConvert -- the explicit conversion node

```
THIRFormConvert(THIRExpr)
  value: THIRExpr          # inner; value.form is the source form
  # result_type + form (inherited) = destination type + destination form
  is_const: bool           # const-qualified borrow -> const helper overload
  move: bool               # last-use into owned sink -> _move helper variant
```

Invariant: `THIRFormConvert` preserves `result_type` and changes only `form` --
this is what distinguishes it from `THIRCoerce` (which changes the TYPE). No
`kind` field: the spike proved the runtime helper is a pure function of
(family(result_type), value.form -> form, is_const, move) -- Optional ->
`optional_to_ptr` / `ptr_to_optional[_move]`; Union -> `to_[const_]ptr_variant` /
`to_value_variant<...>`; Tuple -> `tuple_to_pointer<...>` / `tuple_to_storage[_move]
<...>`. (Open caveat below: whether those four inputs suffice for ALL ~150 direct
sites is what the F1 spike must confirm; if a site needs more, it goes on the node
then, not pre-emptively.)

#### How lowering obtains the form -- pure classifier + emit counter

The form/representation decisions are made today DURING the codegen walk (the
binding-site writers in `_gen_var_decl_code` et al.), but they split cleanly:

- **Form classification is pure-derivable.** Codegen already re-runs the same
  `scan_reassigned_vars` prescan sema runs, so the walk-order input (reassigned /
  rvalue-reassigned / alias) is available BEFORE the emit walk. The pointer-vs-
  optional-vs-tuple-vs-alias choice is a pure function of (resolved type +
  prescan). It is extracted into a shared classifier helper that BOTH the legacy
  codegen path and THIR lowering call -- identical by construction, the same trick
  `resolve_stmt_binding_type` already uses. No new mutable pass; the "pre-pass" is
  the prescan that already exists + pure helpers. (Per-type spellings live on
  `TypeDef`; binding-level classification in a shared `forms` helper, per CLAUDE.md.)
- **Only slot numbering is genuine walk-order state** (`rebind_slots`, `__slot_N`)
  -- reproduced by a per-function emit counter, the established `iter_counter`
  pattern from increment 2.

So the legacy path is touched only by a verified extract-method refactor (the
decision logic is unchanged -> zero diff), and lowering and codegen cannot diverge
because they call one classifier.

#### Insertion rule (dissolves the ~150 direct sites)

Every slot has a form (field / container / `Own` -> STORAGE; param / return /
yield / borrow-local -> BORROW; value type -> VALUE; storage-form locals ->
STORAGE). Callers do not pass raw `dst_form` / `is_const` / `move`; a boundary
API carries the decision:

```python
def required_form(slot) -> Form | None:        # None == no form axis (value type)
    ...
def coerce_form(expr, slot):                    # the single insertion door
    f = required_form(slot)
    if f is None or expr.form == f:
        return expr                             # asserts no axis applies for value
    return THIRFormConvert(value=expr, result_type=expr.result_type,
                           form=f, is_const=slot.is_const, move=slot.move)
```

Today's ~12 predicates + 22 side-sets + per-site re-derivations collapse into two
carried facts read here: the source's `form` and the slot's form. This is the bulk
of the win and the bulk of the risk -- the side-sets encode subtle const / rebind /
suspension / null-state facts the tags must preserve to stay byte-identical.

#### RefType (Open Q 12) -- narrowed dissolution

A borrow-form non-value value is exactly what `Ref[T]` marks today; with `form` on
the expr it is redundant (`to_cpp_stored() -> val_or_ref<T>` is the generic-slot
storage form, `is_ref_param()` rvalue-temp binding is a BORROW param slot, lambda
`-> T&` is a BORROW return). Scope of THIS resolution: THIR introduces NO new
`RefType` use, and `RefType` stays frozen. FULL removal of `RefType` from the type
system is a LATER gate (rung F5/F-final), after the generic-slot (`val_or_ref_t`)
and lambda-return cases are proven -- not blessed up front.

### Form rollout ladder (F1 -> F-final)

The form work is a sub-stream of the THIR migration, sequenced one family/
representation-subset at a time, each rung gated by zero snapshot diffs, each
closing named `THIR_FORM_INVENTORY.md` items. The eligibility gate keeps every
intermediate state correct (anything unsupported stays on the proven AST path,
flag off by default), so "partially migrated" is never "broken." Completion is the
defined end state: the gate excludes nothing form-related and the AST form-codegen
is deleted (F-final). Buggy exhibits (BUGS.md union match-capture, `key=` lambda,
async/await union) are migrated FAITHFULLY (byte-identical, bug preserved -- THIR
makes the conversion visible); fixing them is a separate churn-accepting follow-on
that the migration enables. Migration-complete != bugs-fixed.

| Rung | Scope | Closes (inventory) |
|------|-------|--------------------|
| **F1** *(landed 2026-06)* | single-assignment non-value **record** locals + Optional[record] storage->borrow read (`T&` alias, lvalue `optional_to_ptr`, is_const propagation, record borrow params) + scalar field reads; excludes reassigned/rebound/rvalue-slot, container/cross-module/native records, and calls passing a non-value arg (auto-move). Container locals fold in with F3 | most of section 1 non-value-local + section 4 read |
| **F2** *(landed 2026-06)* | reassigned/rebound locals + Optional borrow<->storage write/return. **Landed:** reseatable `T*` pointer-locals -- **lvalue** reseat (`&(...)`, no slot, F2a) and the **rvalue** `__slot_N` rebind machinery (F2d) -- `->` reads; the optional-field/return **write** from a borrow source, copy (`ptr_to_optional`, F2b) and **move** (`ptr_to_optional_move`, F2e); the storage-Optional **return** + **`None`** write/return (`std::nullopt`, F2c). **Deferred -- separate frontiers, not form cells (AST path):** ctor-MIL (M3 constructor frontier; M1 landed 2026-06 so F2 routes corpus method bodies, and M3a landed 2026-06 for pure-MIL scalar inits -- the `ptr_to_optional` MIL cell is M3b) and the call-arg / call+`copy()`-write sources (non-value call args + auto-move). | section 3b slot machinery + section 4 write/lift (the local + free-function sites) |
| **F3** *(landed, increments 22-28)* | Tuple form (per-element pointer/optional mask). **Landed:** storage->borrow read (`tuple_to_pointer`: borrow-form tuple return + storage-tuple `auto&&` alias locals) + borrow->storage write (`tuple_to_storage`, tuple-field write off a borrow tuple param) for pointer-repr tuples of scalar / F1-record / `Optional[F1-record]` elements; and the tuple-subscript family (statement-shape axis, incr 25-28): value / record / `Optional[record]`-element `t[N].field` reads (`std::get<N>`, `->`/`.` per element form, `deref_check` for unproven Optional) + record-element `t[N].field = /+= <scalar>` writes. **Deferred (AST path):** storage-Name alias sources, reassignable BORROW_TUPLE / OPTIONAL_BORROW_TUPLE, the `tuple_to_storage_move` `Own[tuple]` move arm, loop-var / unpack sources (statement-shape axis), Optional-field/tuple-field writes through a subscript, tuple-literal MIL construction | section 1 tuple, section 4 tuple sites |
| **F4** | Union form (`to_ptr_variant` / `to_value_variant`, value/ptr-variant split, the 3 consumer-dictated exhibits) | section 1 union |
| **F5** | Generic-slot form (`val_or_ref_t` / `val_or_ptr_t` over TypeParamRef) | section 1 generic, section 3c RefType (begin) |
| **F6** | str/bytes view split (Open Q 11 scope extension) | section 3d |
| **F-final** | `RefType` removal + AST form-codegen retirement | section 3c, end state |

**Callable-kind axis (M1-M3), orthogonal to the form ladder.** The form rungs run
over a callable slice that was free-functions-only; the method/constructor frontier
widens *which callables* route, independent of *which type families* do. **M1
(landed 2026-06): plain instance methods** -- `self` modeled as an F1-record
pointer-receiver (`THIRSelf` -> `this`, arrow reads), readonly methods admitted
with a const `self`, value-scalar params only. This is what first put F1/F2 under
the whole-corpus byte-diff over *real* corpus code (every prior form rung routed
zero cases -- their shapes live in methods). **M2 (landed, increment 12): record
params on methods** -- a method-level const-param verdict read from the owning
record's `const_borrow_params`. **M3 constructors / member-init-list: M3a (landed,
increment 14)** -- the whole-ctor `THIRConstructor` node (MIL split from body) + a
MIL-tail emitter, for the pure-MIL scalar slice (flat record, every init hoists,
empty body). **M3b-copy (landed, increment 15)** -- record / `Optional[record]`
MIL fields, copy arm: the `ptr_to_optional` cell (now closed) + `None` + non-own
record-param copy, reusing F2b/F2c in MIL position. **M3b-move (landed, increment
16)** -- own-param `std::move` sources (THIR's first `std::move` emit). **M3b-rvalue
(landed, increment 17)** -- record *value* sources (`_is_record_value_source`:
param name / ctor-call rvalue / param field-read, constructing the field directly)
+ own-optional params + the `copy()`-on-Optional source; `self.<record field>` reads
stay deferred. **M3c-trivia (landed, increment 18)** -- docstring / `pass` non-init
bodies (`THIRNoOpStmt`, no code; the body-brace shape is the only output difference),
the first ctor-body statement shape. **M3c-demotion (landed, increment 19)** -- the
hoist/demotion split: non-hoistable / post-chain-break field inits demote into the body
(lowered via the shared `_body_eligible`/`_lower_stmt` path); only the `chain_broken`
cascade needed explicit reproduction, the rest subsumed by the eligibility gate.
**M3d-1 (landed, increment 20)** -- a single F1 base: `super().__init__` -> a structured
`THIRBaseInit` prepended to the MIL. **M3d-2 (landed, increment 21)** -- multi-base
(parent-order-sorted base inits + the `BaseN.__init__` form) + inherited-field writes
(body branch + `expr_reads_self_field`). The ctor frontier (M3a-M3d) is complete; the
remaining ctor cells are cross-axis-blocked (F3+ field forms, the record body-write rung,
native/generic-record frontiers).

**F1 is the pre-commit gate** (Codex review condition + the spike's real test): an
end-to-end byte-identical lowering of one real non-value function through THIR-
with-form, proving the form facts are DECIDED correctly at lowering across every
optional case in the corpus -- not a hardcoded node. **Explicit success criterion:
if `is_const`/`move` turn out to need walk-state the prescan does not carry, the
"pure classifier" assumption does not fully hold and the node shapes are revisited
BEFORE committing F1.** That is the one risk that could push back up the design.

*Gate MET (2026-06, in-tree implementation): F1 lowers real non-value functions
through THIR-with-form, byte-identical to the AST path across the full corpus with
`--thir-codegen` forced. The criterion held -- `is_const` is a pure sema read
(`FunctionInfo.const_borrow_params` for the param receiver + `ReadonlyType`), never
walk-accumulated `const_indirect_locals` (an F1 receiver is a param, and the const
F1-local case is tracked in a per-function `const_locals` set seeded in source
order); `move` does not enter the read slice. No node-shape revisit was needed.*

*Gate run 2026-06 (throwaway spike, PASSED): over 162 compiled sources,
`_is_const_union_source` (the optional-read const decision) returned True only via
`const_ref_params` (a sema/Phase-2 fact) -- never via the walk-accumulated
`const_indirect_locals`. So `is_const` for the read slice is a pure sema read; the
form classification (`OptionalType` + `uses_pointer_repr()` +
`is_storage_form_optional_source`, i.e. `isinstance(.., TpyFieldAccess)`) is a pure
function of type + expr shape; and `move` does not enter the storage->borrow read
slice (it is an F2 borrow->storage concern, and is seeded from sema's
`function_movable_locals` regardless). The transitive `local->local` const path was
not exercised and, if it arises, is forward-source-order reproducible
(decl-before-use). The pure-classifier assumption holds -- F1 is cleared to
implement.*

## Rollout Plan

### Migration Strategy

The migration should be incremental. The compiler currently has three concerns tangled
together:

- sema as the source of truth for typed program facts
- borrow/move analysis spread across sema and codegen
- codegen reading directly from analyzer internals

Those should be separated in phases. The key sequencing principle:

- **switch codegen to THIR before switching codegen to MIR**

THIR is structurally close to the current codegen input, so it is the right first
boundary. MIR should first become the analysis source of truth, and only later the
emission source of truth.

#### Phase-1 spike validation (2026-06)

A throwaway probe lowered one arithmetic function (`def add(a, b): c = a + b + 1;
return c`) to immutable THIR nodes carrying the sema facts, then emitted C++ from
THIR with **no `SemanticAnalyzer` reference** -- output byte-identical to the
current AST-driven codegen. Confirmed empirically:

- **The boundary is real and the leaf emit layer is already analyzer-decoupled.**
  `result_type` (`get_expr_type`) and `resolved_binop` lower onto nodes with no
  friction, and the existing C++ leaf helpers (`expand_cpp_template`,
  `get_dunder_cpp_template`, `TpyType.to_cpp`) produced the binop emission
  unchanged, just fed from THIR instead of side tables. Most of codegen is
  already a `fact -> string` function; THIR only changes where the facts come
  from. The non-form expression/statement coverage is mechanical breadth, not
  hard depth -- a few focused weeks, low conceptual risk.
- **Three friction points, all already named above as rollout prerequisites,
  confirmed real:** (1) implicit coercions are NOT materialized -- a literal `1`
  kept `result_type = IntLiteral(1)`, so coercions must become explicit
  `THIRCoerce` nodes at use sites, not just copied types; (2) liveness/movability
  facts live in per-function context, not a top-level analyzer table, so lowering
  must run per-function in scope; (3) local declared-types must be captured
  explicitly onto `THIRVarDecl` (codegen currently re-derives them).
- **The form facts (Open Questions 9/11) are the genuine long pole.** The slice
  is all value types, so borrow/storage form never arose -- the spike does NOT
  de-risk it. Form-as-an-IR-fact should be *designed before* the form-carrying
  nodes are written, not retrofitted. *(Done 2026-06: the form design is now
  resolved -- see "Form as a First-Class THIR Fact" + the F1-F-final ladder. A
  second spike validated the `THIRFormConvert` node against the live `convert()`
  chokepoint; F1 remains the pre-commit gate for the lowering-side detection.)*

Net: the non-form Phase-1 is a reasonable bet once the fact set is frozen (post
0.4.0); the form decision is the gating design work the THIR node shapes depend
on.

#### Migration Principles

1. **Preserve behavior first.** Early THIR and MIR work should not intentionally change
   generated code or diagnostics.
2. **Make phase boundaries explicit before changing semantics.** First remove analyzer
   coupling, then move borrow/move logic into MIR, then strengthen enforcement.
3. **Advisory first, safe mode later.** The MIR loan checker must initially preserve the
   current migration-friendly warning behavior. Safe opt-in enforcement is layered on
   after the analysis is stable.
4. **Keep explicit low-level tools.** `Ptr[T]` remains available in both default and safe
   mode; only pointer arithmetic / unchecked pointer fabrication stay in `tpy.unsafe`.
5. **Run old and new analyses in parallel during transition.** MIR diagnostics should be
   compared against existing sema behavior before MIR becomes authoritative.

#### Recommended Rollout

Before any codegen switch, the IR must first be complete enough to replace the current
analyzer coupling:

- **Before THIR-backed codegen**: THIR must cover per-function layout/scan facts,
  module options, explicit coercions, view/literal metadata, generator frame metadata,
  and declared globals.
- **Before MIR-backed codegen**: MIR must preserve narrowing, structured region tags
  for reconstructable control flow, and both return-tier and throw-tier error handling.

1. **Define THIR nodes** in `tpyc/thir/nodes.py`
2. **Implement `lower_module()`** in `tpyc/thir/lower.py`
3. **Add `--dump-thir`** to CLI
4. **Create a `THIRCodeGenContext`** that reads from THIR instead of analyzer
5. **Migrate codegen modules one at a time** (expressions, statements, functions, records)
6. **Remove analyzer references from codegen**
7. **Define MIR nodes** in `tpyc/mir/nodes.py`
8. **Lower THIR -> MIR** in `tpyc/mir/lower.py`
9. **Add `--dump-mir`**
10. **Implement MIR liveness + move/copy passes**
11. **Implement MIR advisory loan checker**
12. **Run MIR checker in parallel with existing sema borrow/move logic**
13. **Make MIR authoritative for ownership/borrow diagnostics**
14. **Add safe opt-in mode** on top of the same MIR analysis
15. **Switch codegen from THIR to MIR** once MIR carries enough information for readable,
    stable emission
16. **Retire old sema/codegen ownership logic**

The distinct-types-via-inheritance fix for the str/bytes generic param ABI
(Open Questions item 8) is **independent of this rollout** -- it operates on the
runtime type layer and the C++-template-keyed paths and does not require THIR/MIR
to land first. It can be scheduled separately whenever the team is ready.

#### Why THIR-Backed Codegen Comes First

Jumping directly from "analyzer-backed codegen" to "MIR-backed codegen" would mix four
independent risks:

- new IR design bugs
- new lowering bugs
- new borrow/move analysis bugs
- codegen porting bugs

Switching to THIR first isolates the representation migration from the ownership-model
migration. MIR can then mature as an analysis artifact before it becomes the executable
source.

#### Behavior Expectations By Stage

- **THIR stages**: no intentional behavior change; output should stay identical
- **Early MIR stages**: analysis/debug only; codegen still reads THIR
- **MIR advisory stages**: diagnostics may be compared or duplicated, but default
  severity stays warning-level for migration-friendliness
- **Safe mode stages**: selected MIR violations become errors only under explicit opt-in
- **MIR-backed codegen**: ownership and control-flow decisions now come from MIR, not
  sema/codegen heuristics

Run the full test suite at each stage. THIR migration should be behavior-preserving;
later MIR stages may intentionally alter diagnostics or move/copy decisions, but only
when the corresponding phase is made authoritative.

---

## MIR Design

### Goal

A **CFG-based IR** with explicit control flow, typed places, and explicit move/borrow
operations. Enables path-sensitive borrow checking, precise liveness, and composable
optimization passes.

### Design Principles

- **Not SSA.** Variables are mutable places, like Rust's MIR. SSA would add phi-node
  complexity without proportional benefit given TPy's ownership model.
- **Pre-monomorphization.** Generic functions remain generic in MIR. C++ templates
  handle instantiation. This keeps the generated C++ readable and interoperable.
- **RAII for drops.** No explicit `Drop` instructions for normal scope exits -- C++
  destructors handle cleanup. `del` statements lower to explicit `StorageDead`.
- **Preserves source structure.** MIR is lowered from THIR but retains enough
  information (variable names, source locations, type annotations) for readable
  C++ emission.

### Core Concepts

#### Basic Blocks

```
BasicBlock
  id: BlockId
  statements: list[MIRStmt]
  terminator: Terminator               # goto, branch, return, panic, switch
```

A function is a list of basic blocks. The first block is the entry point. Control
flow is explicit via terminators:

```
Terminator
  = Goto(target: BlockId)
  | Branch(cond: MIROperand, then_: BlockId, else_: BlockId)
  | Return(value: MIROperand | None)
  | Panic(message: str)
  | Switch(operand: MIROperand, arms: list[(Pattern, BlockId)], default: BlockId)
  | Yield(value: MIROperand, resume: BlockId)     # generator yield point
  | Invoke(call: MIRRvalue, ok: BlockId, err: BlockId, err_local: str | None)
                                               # return-tier @error_return handling
  | Unreachable
```

`Yield` suspends the generator, returning a value to the caller. On resume, execution
continues at the `resume` block. This supports the existing state-machine codegen for
generator functions (`gen_generators.py`).

`Invoke` is used for return-tier `@error_return(E)` calls: if the callee returns an
error via `std::expected`, control flows to `err`; otherwise to `ok`. `err_local`
captures the `__err_opt_N`-style temporary when the surrounding `except` block needs
to read the error payload.

Throw-tier `try`/`except` still needs explicit region metadata in MIR. The lowering
must retain enough structured information to represent nested `try` regions and
exception handlers even after CFG flattening. The exact encoding can be block metadata
or explicit handler tables, but MIR-backed codegen cannot assume "return-tier only".

#### Places

A `Place` identifies a logical storage location. This replaces the current string-based
storage keys in `BorrowTracker`.

Important: places model ownership-relevant storage, not literal C++ object layout. For
example, `list[T]` is backed by `std::vector<T>`, so the element storage is not inline in
the vector object itself. MIR should still model:

- the container object
- the container structure (operations like `append`, `insert`, `del` may replace or shift
  the owned backing storage)
- the element storage region borrowed by `items[i]`, `Span[T]`, iterators, etc.

This lets the borrow checker express "element/view borrow of `items`" without caring
whether the runtime representation is inline storage, heap storage, or a view.

The minimal place set should therefore include both direct places and summarized storage
regions:

```
Place
  = Local(name: str)                         # local variable / local owner slot
  | Global(name: str)                        # module/global storage
  | Capture(name: str)                       # captured outer-scope variable
  | Field(base: Place, field: str)           # record field
  | Index(base: Place, index: MIROperand)    # precise container subscript
  | Struct(base: Place)                      # container structural identity
  | Elements(base: Place)                    # container element storage region
  | Deref(base: Place)                       # pointer dereference

# Examples:
# x           -> Local("x")
# G           -> Global("G")
# x from outer -> Capture("x")
# x.items     -> Field(Local("x"), "items")
# x.items[i]  -> Index(Field(Local("x"), "items"), Local("i"))
# items[*]    -> Elements(Local("items"))
# append(items, v) mutates Struct(Local("items"))
# *ptr        -> Deref(Local("ptr"))
```

Places give the borrow checker precise knowledge of what is accessed. `Field(x, "a")`
and `Field(x, "b")` are distinct -- borrowing one does not conflict with mutating
the other.

`Struct(base)` and `Elements(base)` are intentionally coarser than exact indices. They
match the current TPy safety needs well:

- `items[i]` can borrow from `Elements(items)`
- `Span(items)` / `items[a:b]` borrow from `Elements(items)`
- `append`, `insert`, `del`, slice assignment mutate `Struct(items)` and may invalidate
  loans on `Elements(items)`

This is a good first step even if the compiler later grows exact per-element reasoning.

#### Statements

```
MIRStmt
  = Assign(place: Place, rvalue: MIRRvalue)
  | StorageLive(local: str, type: TpyType, kind: LocalKind)
  | StorageDead(local: str)                   # explicit early destruction (del x)
  | Narrow(local: str, narrowed_type: TpyType, source: NarrowSource)
  | Validate(kind: ValidateKind, place: Place)  # borrow check assertion

LocalKind
  = Value                    # T -- value type, stored directly
  | Pointer                  # T* -- pointer-local (non-value type, stack-allocated slot)
  | Ref                      # T& -- reference to another local (alias)

NarrowSource
  = IsInstance
  | Assert
  | MatchArm
  | NonNull
  | PatternGuard

ValidateKind
  = ActiveLoanConflict
  | UseAfterMove
  | StructuralMutationDuringLoan
  | DanglingBorrowReturn
  | InvalidPtrProvenance
```

`LocalKind` reflects TPy's pointer-variable model (see `OWNERSHIP_DESIGN.md`):
non-value-type locals are `T*` pointing to stack-allocated storage, while value-type
locals are plain `T`. This distinction affects codegen (slot allocation) and borrow
checking (pointer-locals create implicit borrows on their backing storage).

`StorageDead` is emitted for explicit `del x` statements (early variable destruction).
Normal scope exits rely on C++ RAII. Note: `del obj[key]` (container deletion) is a
`Call` to `__delitem__`, not `StorageDead`.

#### Rvalues

```
MIRRvalue
  = Use(operand: MIROperand)                        # plain read
  | Move(operand: MIROperand)                        # move (source dead after)
  | Copy(operand: MIROperand)                        # explicit copy
  | Borrow(place: Place,
           mode: BorrowMode,
           provenance: BorrowKind)                   # create reference
  | Call(target: ResolvedFunction,
         args: list[MIROperand],
         type_args: tuple[TpyType, ...])
  | BinOp(left: MIROperand, op: BinOpKind, right: MIROperand)
  | UnaryOp(op: UnaryOpKind, operand: MIROperand)
  | Literal(value: int | float | str | bool | None)
  | Construct(type: TpyType, fields: list[MIROperand])
  | Aggregate(kind: AggregateKind, elements: list[MIROperand])
  | Coerce(operand: MIROperand, from_type: TpyType, to_type: TpyType)
```

The critical distinction is `Move` vs `Copy` vs `Use`:
- `Use` reads without ownership transfer (value types, references)
- `Move` transfers ownership -- the source place is dead after
- `Copy` creates an independent copy of a non-value type

In the current compiler, this decision is made at codegen time via `_maybe_move()`.
In MIR, it is an explicit instruction decided by the move optimization pass.

Conceptually, `Move` is an ownership-transfer request on a place, not "the variable's
type changed to `Own[T]`". A local binding keeps its base type `T`; MIR decides whether
a particular use site becomes `Use(x)`, `Copy(x)`, or `Move(x)` based on liveness,
uniqueness, and active loans on the underlying place.

#### Borrows vs Loans

A useful distinction:

- **borrow**: the source-language semantic relation ("this value refers to someone
  else's storage")
- **loan**: the MIR borrow checker's active tracked record of that borrow over a place

Example: `y = x` for a non-value type creates a borrow of `x`'s place. The checker then
tracks an active loan on that place while `y` is live. A later `Move(x)` conflicts with
that active loan unless analysis proves `y` is dead.

#### Derived Lifetimes and Provenance

TPy should not expose Rust-style explicit lifetime parameters in ordinary source code.
Instead, lifetimes are derived from MIR loan liveness and carried internally as:

- the place being borrowed
- the CFG region where the loan is live
- the provenance of any derived view / pointer / borrowed return

Conceptually:

```text
LoanInfo
  id: LoanId
  place: Place
  mode: BorrowMode
  kind: BorrowKind
  origin: StmtId | ExprId
  holder: LocalName | TempId | ReturnValue | FieldSink
  live_blocks: set[BlockId]
  provenance: Provenance
```

Where provenance captures where a non-owning value came from:

```text
Provenance
  = FromPlace(place: Place)
  | FromParam(index: int)
  | FromGlobal(name: str)
  | FromCapture(name: str)
  | FromUnknown
  | Join(sources: list[Provenance])
```

Examples:

- `span = items[a:b]` -> provenance from `Elements(Local("items"))`
- `p = ptr(x)` -> provenance from `Local("x")`
- `return self.field` -> provenance from `Field(Local("self"), "field")`
- borrowed value returned from a wrapper -> provenance joined from the source params

This is the internal lifetime model for safe-mode checks. A move, mutation, return, or
escape is legal only if no conflicting live loan reaches that program point and the
provenance proves the source outlives the use.

#### Borrow Kinds and Modes

```
BorrowMode
  = Shared
  | Mutable

BorrowKind
  = Alias                   # whole-container alias (safe through mutations)
  | Field                   # field-level reference
  | Element                 # reference to container element
  | Iterator                # for-loop iterator over container
  | Pointer                 # Ptr[T]
  | View                    # StrView / BytesView / Span-like view
```

These correspond to the existing `BorrowKind` enum in `sema/context.py` (`ALIAS`,
`FIELD`, `ITER`, `ELEMENT`, `PTR`). The key semantic distinction: `Alias` borrows
are safe through container mutations (whole-object reference, not invalidated by
reallocation), while `Element` and `Iterator` borrows are invalidated by structural
mutations (append, insert, del). `Field` borrows are invalidated when the parent
object is reassigned but not by sibling field mutations.

In the current compiler, borrows are side-state in `BorrowTracker`. In MIR they
become explicit `Borrow` instructions, making conflicts visible in the IR. `BorrowMode`
captures whether the use requires shared or mutable access; `BorrowKind` captures where
the borrow came from and what invalidates it.

`Pointer` deserves special treatment: `Ptr[T]` is not "arbitrary raw pointer" in the
language design. It is primarily an explicit nullable reference form. Pointer arithmetic
and unchecked pointer manipulation remain in `tpy.unsafe`; plain `Ptr[T]` operations can
still participate in normal provenance / lifetime analysis.

#### Function Lifetime / Effect Contracts

For ordinary TPy functions, many facts can be inferred and materialized into THIR / MIR:

- `return_borrows_from = {0, ...}`
- `mutated_params = {...}`
- structural invalidation facts for container-like methods
- whether a returned `Ptr[T]` / `Span[T]` / `StrView` is derived from an input place

For native functions implemented in C++, these contracts should usually be explicit,
because the compiler cannot reliably infer them from the definition body. The IR design
therefore needs room for native summaries such as:

- `return_borrows_from`
- `returns_ptr_to`
- `mutates`
- `may_invalidate`
- `readonly`
- `opaque_effects`

These contracts are especially important for core-library functions that construct or
return views (`Span`, `StrView`, `BytesView`), explicit nullable references (`Ptr[T]`),
or iterator/pointer-like adapters.

Absent an explicit contract, native code should be treated conservatively:

- returned provenance may be `FromUnknown`
- mutation / invalidation may be assumed
- advisory mode may warn and reduce optimization
- safe mode may reject lifetime-sensitive uses unless the call is behind an explicit
  escape hatch

### THIR -> MIR Lowering

The lowering pass (`tpyc/mir/lower.py`) converts THIR to MIR:

1. **Control flow desugaring.** `if`/`else` -> `Branch` terminators, `for` -> loop
   blocks with `Goto`/`Branch`, `match` -> `Switch`, `while` -> loop with `Branch`.
   `for/else` and `while/else` desugar to a boolean flag + `Branch` after the loop
   (flag is set on `break`, checked after loop exit). `try`/`except` for
   `@error_return` desugars to `Invoke` terminators for return-tier handling, while
   throw-tier `try` / `except` must preserve enclosing region / handler metadata.
   Chained comparisons (`a < b < c`) desugar to short-circuit `Branch` chains during
   lowering.

2. **Place construction.** Each lvalue expression becomes a `Place`. Field accesses,
   subscripts, and derefs nest naturally.

   Type narrowing must also survive lowering. Branches and match arms that narrow a
   name's type insert explicit `Narrow(local, narrowed_type, source)` statements on the
   dominated path. Later MIR passes and MIR-backed codegen consult these statements to
   build block-local type environments. This avoids losing facts like "in this block,
   `x` is known to be `Foo`" after flattening THIR control flow into basic blocks.

3. **Initial Move/Copy assignment.** The lowering pass inserts `Move` for last-use
   sites (from THIR's `is_last_use` flags) and `Copy` elsewhere. The optimization
   pass may upgrade `Copy` -> `Move` later.

4. **Borrow creation.** Alias assignments (`y = x` for non-value types) become
   borrows of the underlying owner place. Element access and view creation should lower
   to summarized element-storage borrows:

   - `y = x` -> borrow of `Local("x")` (or the owner place behind it)
   - `v = items[i]` -> borrow of `Elements(Local("items"))`
   - `span = items[a:b]` -> view borrow of `Elements(Local("items"))`
   - `p = take_ptr(x)` -> pointer borrow of `Local("x")`

   Exact `Index(base, i)` borrows can be added later for more precision, but the initial
   MIR should support the summarized `Elements(base)` form because it matches the current
   TPy invalidation rules.

5. **StorageLive/StorageDead.** `StorageLive` at variable declaration, `StorageDead`
   at explicit `del` statements.

### MIR Passes

Each pass is an independent function `pass(mir: MIRFunction) -> MIRFunction` or
`pass(mir: MIRFunction) -> list[Diagnostic]`:

#### Pass 1: Liveness Analysis

Standard backward dataflow on the CFG. For each basic block, compute which variables
are live at entry and exit. This replaces `tpyc/liveness.py` with a principled
algorithm that handles branches, loops, and join points correctly.

Result: `LivenessInfo` mapping each statement to the set of live variables after it.

#### Pass 2: Move Optimization

Using liveness info, upgrade `Copy` -> `Move` where the source is dead after:

```
Before:  _tmp = Copy(x)       # x is dead after this point
After:   _tmp = Move(x)       # ownership transferred
```

This replaces the current `_maybe_move()` / `movable_locals` / `all_last_uses`
machinery with a single, clean pass.

#### Pass 3: Borrow Checking

Walk the CFG forward, maintaining per-block borrow state:

```python
BorrowState:
  active_loans: dict[Place, set[LoanInfo]]
  moved_places: set[Place]
```

At each statement:
- `Borrow(place, mode=Mutable, ...)` -- check no conflicting live loans on `place` or overlapping
  parent/child places
- `Borrow(place, mode=Shared, ...)` -- check no live mutable / move-conflicting loans on `place`
- `Move(place)` -- check no active loans that still reach `place`, mark as moved
- `Assign(place, ...)` -- invalidate or conflict with child-place loans as appropriate
- `Assign(Struct(base), ...)` / structural mutation calls -- conflict with loans on
  `Elements(base)` and views derived from them
- Calls with mutated params -- check no conflicting loans on argument places
- `Ptr[T]` creation / use -- treat as explicit nullable-reference loans, not as a fully
  unchecked bypass; pointer arithmetic remains outside this pass in `tpy.unsafe`

For summarized container places, the critical rules are:

- loans on `Elements(base)` represent element refs, spans, iterators, and other views
- mutating `Struct(base)` may invalidate `Elements(base)` loans
- sibling field loans (`Field(x, "a")` vs `Field(x, "b")`) do not conflict unless a
  parent-place operation invalidates both

At branch join points, merge loan states conservatively across reachable predecessors.
The key win over the current AST-based checker is that the analysis is attached to CFG
edges and explicit places rather than string roots and ad hoc freeze/restore snapshots.

Loop headers are merge nodes with pre-loop and back-edge predecessors. Monotone
kill-facts (pointer non-null, parameter provenance, trusted-call-return, type
narrowing) must be meet-merged at the header rather than restored from the pre-loop
snapshot: a fact that the body clears must not re-appear after loop exit. The
current AST-based checker applies a single-pass intersection for all four sets at
loop exit (`tpyc/sema/init_tracker.py::apply_loop_exit_facts`), which is sound for
post-loop uses but remains optimistic for mid-body uses (body analysis starts from
the pre-loop snapshot). In MIR this falls out of standard forward dataflow at the
header and should become a hard correctness requirement for Pass 3, with no
mid-body approximation.

This replaces the current `BorrowTracker` in `sema/context.py` with path-sensitive
analysis. The key improvement: an `if` branch that moves a variable does not conflict
with an `else` branch that borrows it, because they are on different paths.

The same pass can produce different severities depending on enforcement mode:

- **advisory/default**: emit warnings, keep lowering
- **safe opt-in**: elevate selected violations (dangling borrowed return, structural
  mutation while `Elements(base)` is loaned, move with live aliases, invalid `Ptr`
  provenance) to hard errors

The `Validate` statement family exists so MIR lowering and early analysis passes can
materialize the checks that later become diagnostics or hard errors:

- `Validate(ActiveLoanConflict, place)` -- use/mutation conflicts with a live loan
- `Validate(UseAfterMove, place)` -- moved place used again
- `Validate(StructuralMutationDuringLoan, place)` -- structural mutation invalidates
  element/view loans
- `Validate(DanglingBorrowReturn, place)` -- borrowed return escapes owner lifetime
- `Validate(InvalidPtrProvenance, place)` -- `Ptr[T]` escapes or aliases invalidly

#### Pass 4: Value Range Propagation

Forward dataflow tracking integer ranges `[lo, hi]` through the CFG. This replaces
`tpyc/sema/value_range.py` with a CFG-based version that naturally handles loop
induction variables and branch conditions.

Result: at each subscript/deref, whether bounds check / null check can be elided.

#### Pass 5: Dead Code Elimination

Remove statements whose results are never used (no live variables depend on them).
Standard backward pass on the CFG.

### Debugging: `--dump-mir`

```
fn main() -> Void:
  bb0:
    StorageLive(items, list[Int32])
    items = Aggregate(List, [Literal(1), Literal(2), Literal(3)])
    StorageLive(total, Int32)
    total = Use(Literal(0))
    goto -> bb1

  bb1:                                  // loop header
    _iter_has_next = Call(iter.__next__, [_iter])
    branch(_iter_has_next) -> bb2, bb3

  bb2:                                  // loop body
    x = Use(_iter_current)
    total = Call(Int32.__add__, [total, x])
    goto -> bb1

  bb3:                                  // after loop
    Call(print, [Move(total)])
    return
```

### Interaction with Ownership Model

TPy's ownership model is advisory by default. Existing codebases must continue to
compile, so the MIR needs to support two enforcement levels over the same core place /
loan analysis:

- **Default mode (advisory)**: emit warnings, drive move/copy optimization, preserve
  current migration-friendly behavior
- **Safe opt-in mode**: treat a selected subset of ownership / lifetime violations as
  hard errors, with explicit escape hatches still available

TPy is therefore not a globally affine type system (see
`docs/CONSUMING_ITERATION_DESIGN.md`). The MIR should model ownership strongly enough to
support an enforcing mode later, but its default interpretation remains advisory.

**Move/Copy is a place-level decision.** `Move` means "transfer ownership of the
underlying place". In advisory mode, a failed move check may become a warning or may be
lowered back to `Copy` / `Use` depending on the operation. In safe mode, the same check
can be a hard error.

**`Own[T]` requests transfer, it does not make names affine.** When a function parameter
is `Own[T]`, the caller's argument is lowered as a request to `Move(arg_place)`. This is
legal only when the owner place is unique enough at that program point. The local binding
itself does not permanently change type from `Ref[T]` to `Own[T]`; the access mode is
chosen per use site.

**`Ptr[T]` remains available even in safe mode.** The intended meaning of `Ptr[T]` is
"explicit nullable reference", not unrestricted raw pointer. In safe mode:

- plain creation / passing / returning / dereferencing of `Ptr[T]` can remain allowed
- provenance and lifetime of the pointee place are checked
- `Ptr[readonly[T]]` participates as an explicit readonly borrow
- pointer arithmetic, unchecked casts, and arbitrary address fabrication stay in
  `tpy.unsafe` as escape hatches outside the safety guarantee

This preserves migration viability for existing low-level code while still allowing a
stronger safety story for ordinary non-pointer borrows.

**Consuming iteration lowers naturally.** A consuming `for` loop:

```python
for x in items:    # items is last use, consuming __iter__ selected
    process(x)     # x is Own[T], movable
```

Lowers to:

```
_iter = Call(__iter__, [Move(items)])     // consuming overload, items moved
bb_loop_body:
  x = Move(_iter_current)                // element moved out of iterator
  Call(process, [Move(x)])               // x moved into process
```

The THIR's `is_consuming` flag drives the selection of `Move` vs `Use` for the
iterable, and the element variable is naturally movable.

**`del` has two forms.** `del x` (variable destruction) lowers to `StorageDead(x)` in
MIR, enabling early resource release. `del obj[key]` (container element deletion)
lowers to `Call(__delitem__, [obj, key])`. Normal scope-exit destruction is handled by
C++ RAII -- the MIR does not insert drops at scope boundaries.

**`@nocopy` types.** For `@nocopy` types, the move optimization pass can verify that
no `Copy` instructions exist for that type -- any remaining `Copy` is a compile error.
This is cleaner than the current approach of checking during type coercion in sema.

**Borrow checking respects TPy's permissive aliasing.** Unlike Rust, TPy allows
multiple mutable references to the same object (matching Python semantics). The borrow
checker focuses on:
- Iterator invalidation (mutation during iteration)
- Element reference invalidation (structural mutation while element is borrowed)
- Pointer invalidation (reallocation while `Ptr[T]` is outstanding)
- Use-after-move for `@nocopy` types

It does **not** enforce exclusive mutable access (no "aliasing XOR mutability" rule).

### MIR -> C++ Codegen

The codegen backend reads MIR instead of THIR:

| MIR construct | C++ emission |
|---------------|-------------|
| `Move(x)` | `std::move(x)` |
| `Copy(x)` | `x` (C++ copy constructor) |
| `Use(x)` | `x` |
| `Borrow(x, Shared, Alias)` | (variable is `T*` or `T&` -- whole-object reference) |
| `Borrow(x, Shared, Field)` | (variable points to `parent.field`) |
| `Borrow(x, Shared, Element)` | (variable points to `container[i]`) |
| `StorageLive(x, T, Value)` | `T x;` or `T x = ...;` |
| `StorageLive(x, T, Pointer)` | `T __slot_x; auto* x = &__slot_x;` |
| `StorageDead(x)` | `{ /* end scope for x */ }` or explicit destruction |
| `Goto(bb)` | fall-through or `goto` (structured emission avoids goto where possible) |
| `Branch(c, t, f)` | `if (c) { ... } else { ... }` |
| `Switch(...)` | `switch` or `if`/`else if` chain |
| `Invoke(call, ok, err, err_local)` | `auto __res = call; if (!__res) { err_local = __res.error(); goto err; }` |
| `Yield(val, resume)` | state-machine `switch` dispatch |

The codegen reconstructs structured control flow from the CFG where possible (if/else,
while, for) to keep the C++ readable. This is a well-studied problem (structural
analysis / region detection), but it is also one of the biggest migration risks. MIR-
backed codegen should therefore require explicit structured-region tags from lowering:
loop headers/latches/exits, `for/else` and `while/else` regions, `with` guards,
return-tier and throw-tier `try` regions, generator dispatch roots, and short-circuit
comparison regions. "Recover structure from raw CFG alone" is not a realistic
implementation requirement for the first MIR-backed codegen pass.

---

## What Does NOT Change

- **Type checking stays tree-based.** Overload resolution, generic instantiation,
  protocol conformance, type inference -- all remain in sema, operating on the AST.
  These are naturally tree-shaped operations.

- **C++ templates for generics (C++ backend).** No monomorphization in the TPy
  compiler for the C++ backend. Generic functions in MIR carry type parameters, and
  codegen emits `template<typename T>`. An LLVM backend would add a monomorphization
  pass (see Future: LLVM Backend).

- **Parser unchanged.** The parser produces the same AST. THIR lowering is a new
  pass after sema, not a parser change.

- **Test structure unchanged.** Snapshot tests compare generated C++ output. Since
  codegen still produces C++, the test infrastructure works as-is. New snapshot tests
  can be added for THIR and MIR dumps.

- **Mutation propagation stays in sema.** The Phase 2 call-graph fixpoint (transitive
  mutation inference) runs after sema and before THIR lowering. Its results are
  materialized into THIR nodes (`mutated_params`, `is_readonly`). MIR borrow checking
  consumes these facts but does not recompute them.

---

## Phasing and Dependencies

```
Phase 1 (THIR):
  1.1  Define THIR node types                             tpyc/thir/nodes.py
  1.2  Implement THIR lowering pass                       tpyc/thir/lower.py
  1.3  Add --dump-thir CLI flag                           tpyc/cli.py
  1.4  Create THIRCodeGenContext                           tpyc/codegen_cpp/context.py
  1.5  Migrate codegen to read from THIR                  tpyc/codegen_cpp/*.py
  1.6  Remove analyzer reference from codegen             tpyc/codegen_cpp/context.py
  1.7  Add THIR snapshot tests                            tests/

Phase 2 (MIR):
  2.1  Define MIR types (Block, Place, Stmt, Rvalue)      tpyc/mir/nodes.py
  2.2  Implement THIR -> MIR lowering                     tpyc/mir/lower.py
  2.3  Implement liveness pass                            tpyc/mir/liveness.py
  2.4  Implement move optimization pass                   tpyc/mir/move_opt.py
  2.5  Implement borrow checking pass                     tpyc/mir/borrow_check.py
  2.6  Implement value range pass                         tpyc/mir/value_range.py
  2.7  Add --dump-mir CLI flag                            tpyc/cli.py
  2.8  Migrate codegen to read from MIR                   tpyc/codegen_cpp/*.py
  2.9  Remove old liveness.py, BorrowTracker,             tpyc/liveness.py,
       value_range.py                                     tpyc/sema/context.py,
                                                          tpyc/sema/value_range.py
  2.10 Add MIR snapshot tests                             tests/
```

Phase 1 is a prerequisite for Phase 2. Within each phase, steps are sequential except
that snapshot tests (1.7, 2.10) can be added incrementally alongside each step.

---

## Future: LLVM Backend

The MIR design intentionally keeps the door open for an LLVM backend. This section
documents what that would require and how C++ interop is preserved.

### Pipeline

The MIR stays backend-agnostic. The backend choice determines which lowering runs
after the shared analysis passes:

```
                        ┌─> C++ codegen (structured C++ emission)
THIR -> MIR -> passes ──┤
                        └─> LLVM lowering (future)
                              ├─ monomorphization pass
                              ├─ drop insertion pass
                              └─ LLVM IR emission
```

Passes 1-5 (liveness, move optimization, borrow checking, value range, dead code)
are shared. The backends diverge only at the final emission stage.

### What LLVM Requires Beyond C++

| Concern | C++ backend | LLVM backend |
|---------|-------------|-------------|
| **Generics** | C++ templates | Monomorphization pass: stamp out concrete versions of each generic function for every used type combination |
| **Drops** | C++ RAII (implicit) | Explicit drop insertion pass: compute drop points at scope exits, `Move` sites, and early `StorageDead` |
| **STL types** | Direct use (`std::vector`, `std::string`, etc.) | Link against libstdc++/libc++ and call through C-ABI wrappers, or provide a TPy runtime library |
| **Name mangling** | C++ compiler handles it | Emit mangled names following the platform ABI (Itanium/MSVC) |
| **Exceptions** | C++ exceptions / `std::expected` | LLVM `invoke`/`landingpad` for unwinding, or keep `std::expected` via C-ABI calls |

The **monomorphization pass** is the largest addition. It runs on MIR before LLVM
lowering, replacing generic type parameters with concrete types and duplicating
function bodies. This is the same approach Rust takes (monomorphize on MIR, then
lower to LLVM IR). The C++ backend skips this pass entirely.

The **drop insertion pass** walks the CFG and inserts destructor calls at every point
where a variable goes out of scope or is moved. The C++ backend skips this because
C++ RAII handles it implicitly. For LLVM, drops are explicit `Call` instructions to
destructor functions.

### C++ Interop Without Generating C++

Interop is an **ABI contract**, not a source-level dependency. LLVM-generated machine
code can interoperate with C++ code because both follow the same platform ABI.

| Direction | Mechanism |
|-----------|-----------|
| **TPy calls C++** | `@native` declarations provide the C++ function signature. The LLVM backend emits a call using the platform's C++ ABI (same calling convention, name mangling). The C++ library is linked at link time. |
| **C++ calls TPy** | The TPy compiler generates a C++ header (`.hpp`) declaring the TPy-compiled functions with proper mangling. C++ code `#include`s the header and links against the TPy-compiled object files. |
| **Shared types** | Types like `std::vector<int32_t>` have a fixed ABI layout. LLVM-generated code can construct/read/write them if it knows the layout. Alternatively, C-ABI wrapper functions handle type construction/access. |

This is proven by prior art:
- **Rust** interops with C++ via `cxx`/`bindgen` without generating C++ source
- **Swift** interops with ObjC/C++ through ABI compatibility
- **Clang itself** compiles C++ to LLVM IR -- so LLVM IR is inherently ABI-compatible
  with C++ compiled by Clang

### Runtime Library Strategy

The current C++ backend relies on the C++ standard library (`std::vector`,
`std::string`, `std::optional`, `tpy::ordered_map`, etc.) plus TPy's runtime headers
in `runtime/cpp/include/tpy/`. For an LLVM backend, two viable strategies:

1. **Link against the C++ runtime.** Compile `runtime/cpp/` with a C++ compiler into
   a static/shared library. LLVM-generated code calls into it via C-ABI wrapper
   functions. This reuses all existing runtime code. The wrappers are thin: `vec_push`
   calls `std::vector::push_back`, `str_len` calls `std::string::size()`, etc.

2. **Native TPy runtime (long-term).** Rewrite performance-critical runtime components
   (vector, string, hash map) in TPy itself or in C with LLVM-friendly layouts. This
   eliminates the C++ stdlib dependency but is a large effort. Practical only if/when
   TPy is self-hosting.

Strategy 1 is the pragmatic starting point. The C-ABI wrappers can be auto-generated
from the existing runtime headers.

### MIR Design Implications

The MIR as currently designed requires **no structural changes** for LLVM support.
The key decisions that keep it backend-agnostic:

- **Not SSA**: LLVM IR is SSA, but LLVM's `mem2reg` pass converts alloca-based code
  to SSA automatically. MIR places lower to allocas, and LLVM optimizes from there.
- **Explicit Move/Copy/Borrow**: these map to LLVM operations regardless of backend.
  `Move` -> load + store + drop source. `Copy` -> load + store (or memcpy).
- **Typed places with `LocalKind`**: `Value` locals -> alloca. `Pointer` locals ->
  alloca holding a pointer. Natural LLVM lowering.
- **Backend-specific passes**: monomorphization and drop insertion are additional
  passes in the LLVM pipeline, not changes to the shared MIR.

### Not a Near-Term Goal

The LLVM backend is a future possibility, not a current priority. The C++ backend
remains the primary target because:
- Readable C++ output is valuable for debugging, auditing, and interop
- C++ templates avoid the complexity of compiler-side monomorphization
- The C++ ecosystem (build systems, sanitizers, profilers) is directly usable
- The runtime library is already written in C++ headers

The IR design simply ensures that this path is not closed off. If/when the LLVM
backend becomes desirable (e.g., for faster compilation, LTO across TPy modules,
or eliminating the C++ compiler dependency), the MIR is ready.

---

## Open Questions

### Implementation Notes

- **Dynamic / opaque values.** Define how `Any`, dynamic `__getattr__` / `__setattr__`,
  namespace-style objects, and opaque native objects lower to coarse summarized places
  and how they degrade analysis precision.
- **Native contract surface.** Decide the exact user-facing annotation/decorator syntax
  for native lifetime/effect summaries such as `return_borrows_from`, `returns_ptr_to`,
  `mutates`, and `may_invalidate`.
- **Safe-mode boundary.** Spell out which operations are inside the safety guarantee and
  which remain explicit escape hatches (`tpy.unsafe`, pointer arithmetic, unchecked
  casts, opaque native code without contracts).
- **Rebind vs mutate.** Make the rule explicit during implementation that rebinding a
  name creates a new owner/place binding, while mutation changes an existing place.
- **Worked examples.** Add a few focused examples once implementation starts,
  especially for borrowed returns, `Ptr[T]`, views, captures, and structural
  invalidation.
- **Structured MIR metadata.** Decide the exact representation of the region tags needed
  for readable MIR-backed C++ emission.
- **Native default conservatism.** Define the default behavior when a native function
  lacks an explicit lifetime/effect contract in advisory mode vs safe mode.

1. **THIR granularity for match/case.** Match arms have complex pattern-matching
   logic. Should THIR preserve the high-level `THIRMatch` with structured arms, or
   desugar patterns into explicit comparisons? Recommendation: keep structured -- the
   `match` codegen already handles this well, and desugaring loses readability.

2. **MIR for top-level statements.** Module-level code (globals, top-level expressions)
   uses a different variable model (pointer slots). Should this go through MIR, or
   should MIR only cover function bodies? Recommendation: MIR for function bodies
   only, at least initially. Top-level code has simpler control flow and less need
   for path-sensitive analysis.

3. **CFG reconstruction for codegen.** Emitting readable C++ from a CFG requires
   reconstructing structured control flow. This is non-trivial: `break`/`continue`
   with labels, `for/else`/`while/else`, `with` statement guards, `try`/`except`
   error-return patterns, and generator state machines all have structured C++
   emission patterns that must be recovered from the CFG. Since all MIR is lowered
   from structured Python, the CFG is always reducible -- but the reconstruction
   still needs careful handling of each pattern. Recommendation: tag MIR blocks with
   their source-level structure during lowering (loop headers, if-then/else, with
   guards) to simplify reconstruction, rather than recovering structure purely from
   the CFG topology.

4. **Incremental adoption.** Should codegen support both THIR and AST input during
   migration, or is a big-bang switch acceptable? Recommendation: dual-mode during
   migration -- each codegen module can be switched independently, verified by running
   the full test suite.

5. **Separate THIR and MIR codegen backends.** During Phase 2, codegen switches from
   THIR to MIR. Should both backends coexist permanently (e.g., THIR backend for fast
   debug builds, MIR backend for optimized builds)? Recommendation: single MIR backend
   once Phase 2 is complete. The MIR pass pipeline can be shortened for debug builds
   (skip optimization passes).

6. **Generator functions in MIR.** Generator functions are currently lowered to state-
   machine structs in codegen (`gen_generators.py`). Should this transformation happen
   during THIR -> MIR lowering (generators become explicit state machines in MIR), or
   should MIR represent generators with `Yield` terminators and defer the state-machine
   transform to MIR -> C++ codegen? Recommendation: `Yield` terminators in MIR --
   this keeps MIR closer to the source semantics and lets the state-machine transform
   remain a codegen concern. But this means MIR passes (liveness, borrow checking) must
   understand that `Yield` suspends and resumes, which complicates dataflow analysis.

7. **String/bytes view borrow tracking.** Sema currently tracks `StrView`/`BytesView`
   borrows separately from `BorrowTracker` (via `str_source_borrows`,
   `bytes_source_borrows`). Should MIR unify these with the general `Borrow`
   instruction, or keep them separate? Recommendation: unify -- a `StrView` borrowing
   from a `str` variable is conceptually the same as any other borrow. The `BorrowKind`
   may need a `View` variant to capture the "invalidated by any mutation of source"
   semantics.

8. **Generic param ABI for TPy types with storage/param split.** Several TPy types
   have a storage/param C++ split: `str` (storage `std::string`, param
   `std::string_view`), `String` (storage `std::string`, param `const std::string&`),
   `bytes` (storage `std::vector<uint8_t>`, param `std::span<const uint8_t>`),
   `bytearray` (storage `std::vector<uint8_t>`, param mutable ref). Non-generic
   codegen handles the split position-aware (param positions emit the param
   formatter, storage positions emit the storage formatter). Generic codegen uses
   the runtime trait `param_val_or_ref_t<T>` keyed on the C++ storage type, which
   cannot distinguish `str` from `String` (both `std::string`) or `bytes` from
   `bytearray` (both `std::vector<uint8_t>`). Net effect today: generic-T-over-str
   pays a `std::string` materialization at every call site (SSO covers short
   literals; long literals heap-allocate once per call). C++ template instantiation
   erases TPy-type identity by the time it sees `T`; no runtime trait keyed on the
   C++ type can recover it.

   No fix is unambiguously best. The honest design landscape:

   | Approach | Idiomatic C++ | `vector<str>` interop | Perf gap closed | Cost |
   |----------|---------------|-----------------------|-----------------|------|
   | **Current state (accept asymmetry)** | yes | preserved | no (small gap) | none |
   | **Distinct C++ types** (`auto_string : public std::string`) | yes | **broken** -- `vector<auto_string>` is not `vector<std::string>` | yes | medium runtime + audit churn |
   | **Codegen monomorphization** (per-call function emission, no template) | yes (output-wise) | preserved | yes | heavy compiler internals (instantiation registry, cross-module emission) |
   | **Descriptor template parameter** (`template<class TDesc>` with `TDesc::storage`, `TDesc::param`) | **no** -- compromises readable C++ output goal | preserved | yes | medium codegen churn but readers must learn descriptor pattern |
   | **Drop the split entirely** | yes | preserved | no (bigger gap, applies to non-generic too) | none |
   | **Trait specialization on shared C++ types** | yes | preserved | yes | unsound -- conflates `str`/`String` and `bytes`/`bytearray`; rejected |
   | **Auto-downgrade `T=str` to `T=StrView` for literals** | no | preserved | yes | unsound -- signature-level safety check can't cover body-side dangling cases; rejected |

   The three idiomatic options each pay a distinct cost. There is no row that wins
   all three of `idiomatic / vector interop / perf gap closed` without paying a
   real cost somewhere.

   **Codegen monomorphization** is the cleanest long-term path *if* the perf gap
   ever becomes worth solving. Compiler emits one C++ function per `(generic, TPy
   type args)` instantiation instead of a single template. Each emitted function
   uses normal C++ types (no descriptors, no traits, no `auto_string` wrapper) --
   `inline std::string echo_str(std::string_view x) { return std::string(x); }`
   reads the way C++ developers expect. Vector interop preserved because storage
   types stay unchanged (`list[str]` still `std::vector<std::string>`). Cost is
   compiler-internal: instantiation registry, cross-module emission rules, header
   placement for inline functions, generic methods/classes/`Fn[..., T]`/protocol
   integration. Estimate: 2-4 weeks of focused work.

   **Distinct C++ types** (auto_string approach) is the lighter-touch idiomatic
   option but pays its cost user-visible: existing user code that does `@native`
   interop with `std::vector<std::string>` against TPy `list[str]` would have to
   migrate to `list[String]` (which stays `std::vector<std::string>`). Mechanical
   migration but real surface change. Estimate: 1-2 weeks.

   **Current state** is the pragmatic answer. The perf gap is small in practice
   (SSO covers the common case of short literals; longer literals through pure
   pass-through generics is a rare pattern); users who hit a real hot path can
   write `def f(s: StrView)` explicitly. Aligns with TPy's "opt-in constraints
   for hot paths" philosophy: the perf gap is the cost of *not* opting in to
   compiler complexity. Recommendation: stay here unless measured workloads
   justify the upgrade.

   **Descriptor template parameter** -- documented for completeness, but the
   non-idiomatic generated C++ output (`f<tpy::str_desc>(...)` instead of
   `f<std::string>(...)`) compromises a stated TPy goal: readable C++ output for
   debugging, auditing, and interop. Demoted to "considered but compromises
   primary goal." Could still serve as a fallback for future TPy types whose
   semantics genuinely cannot be expressed via distinct C++ types, but for the
   str/bytes case the output cost outweighs the benefits.

   **Drop the split** is the simplification answer. Single representation per
   TPy type (`str` always `std::string`, `bytes` always `std::vector<uint8_t>`).
   `StrView`/`BytesView` remain as explicit opt-in for view semantics. Predictable,
   uniform, no special machinery. Pays the materialization cost at every str
   param boundary (SSO covers it for short literals). Worth considering if/when
   the architectural simplification becomes more valuable than the optimization.

   **Scheduling**: no work planned. The current state is the safety floor. If the
   perf gap becomes worth fixing (driven by measured workloads, not preemptive
   optimization), the recommended target is codegen monomorphization. That work
   does *not* require THIR/MIR to land first but probably benefits from being
   done concurrently with the THIR codegen migration to avoid double-churn.

9. **Tuple form as a first-class type fact.** RESOLVED 2026-06 -- see "Form as a
   First-Class THIR Fact" (THIR Design). The exhibit inventory below stands as the
   ground-truth surface the design must subsume; the resolution is: a `form` tag on
   the THIR EXPRESSION (not the type) + an explicit `THIRFormConvert` node, with
   the recommendation in this item's last line adopted (form tag + explicit
   conversion nodes) but PLACED ON THE EXPR for the positional reasons given there.
   (A full ground-truth map of the
   form-dispatch surface this item -- plus items 11/12 -- must subsume is in
   `docs/THIR_FORM_INVENTORY.md`, the bootstrap artifact for the form design.)
   Today `TupleType` is a single sema type
   whose C++ representation depends on context -- borrow form (`tuple<T*,...>`,
   one pointer-element shape for every non-value element since the tuple
   borrow-pointer unification; generic elements via `val_or_ptr_t<T>`) at
   param/return/local/frame boundaries, storage form (`tuple<optional<T>,...>`,
   `tuple<T,...>`) at field/container/`Own[]` boundaries. (See
   `LANGUAGE_FEATURES.md` "Borrow Form vs Storage Form" for the canonical definition
   of these forms; this item is specifically about elevating the distinction to a
   first-class IR fact.) Codegen reconstructs which form is needed at each site and
   inserts conversions (`tuple_to_storage[_move]` with per-element dest-shape
   dispatch, `tuple_to_pointer`, `tuple_value_to_borrow`, `to_storage_elem`,
   `to_pointer_form`, `to_val_or_ptr`). The unification fixed the silent-copy
   bug class (a param-/local-/call-rooted reference-member tuple now ALIASES at
   yield/return, matching CPython; the borrow lives in a pointer-holding frame
   field, which the resumable-frame model CAN express -- the prior assessment
   that the share fix was THIR-gated proved wrong), but it did so by adding
   more consumer-side dispatch: every site that READS a tuple element as a
   value re-derives the form. The consumer-site inventory THIR must subsume
   with structural conversion/access nodes:
   - subscript read (`_gen_subscript` tuple branch: raw pointer vs
     `tuple_elem_ref` for generic slots vs `optional_to_ptr` lift),
   - field access / method call on a subscript (`->` vs `.` via
     `_tuple_subscript_yields_borrow_ptr`),
   - value contexts (`gen_expr_deref` deref of borrow subscripts),
   - unpack binding (`unwrap_ref(tuple_elem_ref(std::get<i>(...)))`),
   - print/repr and hash (runtime `print_element` / `__hash__` `T*` deref
     overloads),
   - comparison (`tpy::tuple_eq` / `tpy::tuple_lt` routing in the binop
     emitter, plus the `in`-needle storage lift),
   - construction slots (`_tuple_literal_slot_info` + address-of /
     `tuple_value_to_borrow` / `to_val_or_ptr<Dest>` value rendering),
   - boundary wraps (`_maybe_wrap_tuple_to_pointer` / `_to_storage`, the
     call-arg bridge, field writes, return/yield conversion, the await-arg
     lift).
   Remaining open exhibits of the bug class: nested tuples where outer/inner
   forms disagree (BUGS.md nested-tuple entries), rvalue tuple-of-records into
   borrow-form slots (BUGS.md rvalue address-of entry), the rvalue GENERIC
   tuple element gap, and the recursive-union-wrapper durable member (excluded
   from the `T*` form). A particularly sharp exhibit is the `key=` lambda over
   a generic-element tuple (`sorted(pairs, key=...)` /
   `min(a, b, key=...)` where `pairs: list[tuple[T, Int32]]`): the lambda's
   param form is reconstructed at its DEFINITION site, but the form it actually
   needs is decided by the CONSUMER -- `builtin_sorted_key` calls `key(items[i])`
   with a STORAGE-form element, while `min_key`/`max_key` are handed the
   BORROW-form function args, so one lambda definition cannot satisfy both
   consumers under the current model. (`min`/`max` additionally need a
   borrow->storage RETURN conversion on the result.) Value-type keys (str/int)
   compile because borrow and storage forms coincide; reference-type keys fail
   the C++ build with no TPy diagnostic. With form as an explicit IR fact the
   lambda param carries its consumer-dictated form and the conversion is a
   visible node, not a definition-site guess. (BUGS.md key-lambda generic-element
   entry.) Another sharp exhibit is the **async/await union return** (B41 Union
   sibling, BUGS.md): aligning an `async def -> A | B` to the sync borrow
   convention requires classifying the await-result union frame-local as
   pointer-variant (`std::variant<A*,B*>`), but that single type-based
   classification reaches a *different* consumer -- a direct storage binding in
   the same coro body (`pet = h.pet`, union field -> local) -- whose async
   binding site emits a plain assignment with no `to_ptr_variant` lift, while
   the sync var-decl for the identical source emits one. So one form
   classification cannot satisfy both the await-result consumer (wants borrow)
   and the direct-binding consumer (whose binding site doesn't convert), and the
   fix splinters into either per-binding-site lifts or await-target-specific
   classification. (The scalar pointer-repr Optional case has no such split
   because its local form is uniformly `T*` -- the value/pointer-variant binding
   duality is specific to unions.) The same union value/pointer-variant split
   recurs at a third site, the **`match` capture of a genuine-union subject**
   (`def m(x: A | B): match x: case q:`): the capture `q` is hoisted in STORAGE
   form (value-variant `std::variant<A,B>`) while the subject is BORROW form
   (pointer-variant `std::variant<A*,B*>`), so the bind is a C++ type error with
   no TPy diagnostic -- and, unlike the await/field case, the *aliasing form a
   union capture should even take* (pointer-variant alias vs value-variant
   copy-out vs per-variant narrowing) is undesigned, so this exhibit is also a
   design question, not only a missing conversion node. (A *narrowed* union
   subject hits a related but distinct mismatch -- it analyzes as a record match
   against pointer-variant storage. BUGS.md union-match-capture entry.) With form
   an explicit IR fact, each binding's
   borrow<->storage conversion is a visible lowering node regardless of whether
   the source is an await payload or a field read, so the async and sync binding
   paths converge instead of diverging by consumer site. Each of these was/is
   handled by touching consumer-side dispatch sites; the IR fact replaces all of
   it with explicit conversion nodes. THIR should make form an explicit type
   fact (either two distinct tuple types, or a form tag on one), so conversion
   sites become visible in the IR rather than reconstructed in codegen.
   Recommendation: form tag on `THIRTupleType` with conversions emitted as explicit
   THIR nodes during lowering -- analogous to how borrows are explicit in MIR.

10. **Covariant return for polymorphic-owner types.** TPy lowers `Own[T]` to
    `std::unique_ptr<T>`, and C++ does not support covariant return on
    `unique_ptr` (only on raw `T*` / `T&` -- a deliberate, repeatedly-reaffirmed
    C++ language restriction, see P0670's rejection). This blocks the natural
    pattern of a method overriding a `@dynamic`-protocol slot with a narrower
    return type (e.g. `Dog.replicate(self) -> Own[Dog]` refining
    `Cloneable.replicate(self) -> Own[Cloneable]`, or
    `BaseException.clone(self) -> Own[BaseException]` refining
    `Throwable.clone(self) -> Own[Throwable]`). An attempt to support it on the
    C++ backend (a sema covariant-return acceptance rule + a
    `tpy::narrowing_cast<>` codegen bridge that emits the vtable slot at the
    parent's wider signature and downcasts at concrete-typed call sites) worked
    but introduced a divergence between the TPy declaration and the emitted C++
    signature, plus a cluster of edge cases (multi-protocol ambiguity, overload
    matching, the narrowing-cast hardening). It was dropped: the cost/benefit
    (mostly a naming preference -- `Box[BaseException]` vs the existing
    `Box[Throwable]` convention) did not justify the machinery, and the existing
    `Box[Throwable]` + virtual-`__raise__` convention (Phase 20) already handles
    polymorphic exception storage and dynamic-type recovery (`raise stored /
    except ConcreteType`). A backend that controls codegen below the C++ language
    layer (LLVM IR, or the MIR -> C++-or-LLVM split here) has no covariant-return
    restriction: raw pointers in vtable slots + a smart-pointer wrap at the call
    boundary (the standard pre-2011 C++ idiom, and what LLVM-targeting languages
    like Rust/Swift do by choice) makes this a clean codegen rule. Revisit when
    the backend question opens up; until then, declare polymorphic-protocol method
    returns at the protocol's own type (`Own[Cloneable]`, `Own[Throwable]`).
    Recommendation: handle at MIR -> backend lowering, not as a C++-backend
    sema/codegen feature.

11. **Uniform local model: every non-value local as slot + alias, with late
    representation folding.** THIR HALF RESOLVED 2026-06 -- see "Form as a
    First-Class THIR Fact". The THIR-era decision: carry the local representation
    VERBATIM (`cpp_local_representation`, the `LocalCppForm` analog) as non-semantic
    compatibility metadata, byte-identical to today's eager choice. The
    late-representation SELECTION + mem2reg FOLD described below stays MIR-era (it
    is the explicit normalization goal there); THIR does not attempt it. The
    remainder of this item is the MIR design (preserved below).

    Today the C++ shape of a non-value (or
    pointer-repr-tuple) local is decided EAGERLY at the binding site, by a
    zoo of per-shape mechanisms: `T&` ref binds and `auto&&` tuple aliases
    (single-assignment borrows), `T*` pointer-locals + hoisted
    `std::optional<T>` rvalue slots (`rebind_slots`, reassigned borrows),
    `std::optional<T>` optional-locals (deferred init, sync), the walrus
    variants of each, `tpy::frame_slot<T>` (resumable frames),
    `std::tuple<..., T*>` borrow-form tuple locals, and storage-form tuple
    locals -- tracked across `LocalCppForm` (now including `BORROW_TUPLE`)
    plus side sets that remain the classifier's backing store. Each
    mechanism re-implements init-deferral, rebinding, and aliasing slightly
    differently (operator= vs emplace vs lift), which is where the
    `optional` brace-init corruption class, the default-construct-before-
    assign waste, and the tuple owning/alias rebind rejection all came
    from. The MIR-native model dissolves this: every local is a PLACE (a
    slot owning storage, or a borrow of another place); binding kinds are
    explicit (own-init, alias, rebind); representation selection (direct
    `T`, `T&`, `T*` + slot, `optional<T>` / `frame_slot<T>`,
    pointer-element tuple) becomes a LATE per-place decision driven by
    facts the place already carries -- rebound? crosses a suspension?
    address escapes? null state needed? -- followed by a mem2reg-style
    FOLD that collapses single-binding straight-line places back to plain
    direct bindings so generated C++ stays readable and the hot paths
    (param borrows, loop vars) pay nothing. Provenance/escape soundness
    also unifies: the per-name fact sets sema accumulates today
    (`owns_fresh`, `owning_storage`, `ephemeral_borrow_vars`,
    `safe_to_return_vars`) become properties of the place's loans,
    compositional through aliases, ternaries, and walrus by construction
    instead of per-shape propagation rules. Sub-question: whether the C++
    backend should emit ONE deferred-storage primitive everywhere
    (`frame_slot<T>` in sync bodies too, with the state-aware-destruction
    TODO removing its alive bool) or keep `optional<T>` for sync --
    uniformity favors the former; decide when the fold pass exists so the
    choice is measurable. Pre-IR stopgaps this item subsumes: the tuple
    rvalue-slot design + owning/alias mix (landed pre-IR -- borrow-form
    slot + flow-correct owning fact + `BORROW_TUPLE`, with the side sets
    still the backing store rather than a unified place model), and the
    eager per-site binding decisions in `_gen_var_decl_code` /
    `_gen_named_expr` / the loop binders that it leaves scattered.
    Recommendation: make places-with-late-representation the MIR
    locals model (the natural reading of `Place`/`LoanInfo` above), and
    treat the C++ emission of each representation as a small backend menu
    the fold pass picks from. Sequencing (agreed): the representation
    model + fold are IR-ONLY -- building places/CFG/liveness against the
    AST would be writing MIR badly, twice. The one piece worth pulling
    forward pre-IR if the migration is not imminent is the sema-side
    provenance consolidation (one BindingProvenance record replacing the
    per-name fact sets; TODO.md entry carries the decision rule). STATUS:
    the storage + flow-plumbing half of that consolidation LANDED pre-IR --
    six name-keyed escape/ownership fact sets are now one `BindingProvenance`
    record per local (`tpyc/sema/context.py`) merged by one lattice-driven
    routine (`flow_facts.merge_binding_provenance`). What remains IR-only is
    the "root place + binding kind + durability" ONE-derivation model: the
    expression-walking derivers were deliberately left untouched (unifying
    them is the AST-side place model this item defers), and
    `ephemeral_borrow_vars` stays separate (loop-region-scoped, no flow
    merge). `BindingProvenance` is the proto-LoanInfo this item migrates.

    Scope extension: str/bytes VIEW locals belong under this umbrella too,
    even though they are value types lowered by a separate mechanism today
    (the `str_vars`/`bytes_vars` view-tracking facts + a binary
    view-XOR-owned-per-variable decision, e.g. `mark_view_reassigned_from_owned`
    promoting the whole local to `std::string`). The place/slot model says
    the variable stays the borrow form (`string_view`) and an owned-source
    assignment lands in a storage slot bound to it, with the fold collapsing
    to plain `std::string` when the slot is the only source (today's
    always-owned behavior). The win over the current binary choice is the
    MIXED case -- a local fed by a literal/param in one branch and an owned
    temporary in another no longer materializes the borrowed branches into
    `std::string`. As with the rest of item 11 this is fold-dependent (the
    fold must collapse the common always-view and always-owned cases or both
    regress to two C++ variables), so it is IR-era, not a pre-IR change. The
    current binary mechanism is sound (extra copy in mixed cases, never a
    dangle), so this is a quality/uniformity gain, not a correctness fix.

12. **Fate of the sema `Ref[T]` wrapper.** RESOLVED 2026-06 (narrowed) -- see
    "Form as a First-Class THIR Fact". `Ref[T]` dissolves into the borrow-form tag;
    at THIR a borrow-form expr IS what `Ref[T]` marked. Scope of the resolution:
    THIR introduces NO new `RefType` use and it stays frozen NOW; FULL removal from
    the type system is a later gate (rung F5/F-final), after the generic-slot
    (`val_or_ref_t`) and lambda-return cases are proven. Detail below.

    `RefType` is the sema-level
    "borrowed, not owned" marker: auto-inserted by `make_ref` on
    function/method params and returns, field/subscript access results, and
    iterator elements; never user-written. Production is centralized and
    disciplined, but consumption is split between two oracles: codegen
    strips the wrapper at function entry (`var_types` is built via
    `unwrap_ref_type`) and re-derives borrow-ness positionally from
    `is_value_type()`, while compatibility treats `Ref[T] ~ T` in both
    directions and inference canonicalizes it away per position
    (`to_owned_storage_form` for owned slots, `to_bare_slot_form` for
    bare-T slots, both in `sema/type_ops.py`). The strip-to-consume ratio
    across the compiler is roughly 8:1. What genuinely rides on the wrapper
    today: copy-into-storage detection (warning when a borrow is silently
    copied into a field/container), generic reference preservation
    (`U=Ref[Point]` -> `val_or_ref<Point>` for iterator combinators and
    `map(identity, ...)`), and lambda trailing return types (`-> T&`).
    Decision: keep `RefType` until THIR, but treat it as FROZEN -- do not
    extend it to new positions (each one adds strip sites and
    inference-leak surface); new borrow-form facts go on AST nodes per the
    migration rules in CLAUDE.md. At THIR lowering, `Ref[T]` dissolves into
    the explicit form fact of items 9 and 11: the borrow-vs-storage form
    tag plus explicit conversion nodes carries everything the wrapper
    encodes, THIR types do not contain `RefType`, and the stripping fabric
    disappears with it.

13. **Cross-module nominal identity as a single carried fact.** Distinct
    records/exceptions sharing a short name across modules are kept distinct
    by their module qname, but that invariant is currently enforced at ~5
    phase-specific sites keyed on the qname rather than from one object every
    consumer routes through: the resolver mints the qname from the import
    tuple, sema (`isinstance` / constructor record resolution) and codegen
    (`get_record_for_type`, `record_qualification`) re-resolve by qname, and
    `make_union` / `coercions` / `type_ops` compare qnames. No single
    chokepoint exists pre-IR -- that object *is* THIR. A related seam: the
    two type-to-C++ paths use different identity rules (`NominalType.to_cpp`
    keys `native_cpp_names` by short name with a qname fallback, while
    codegen's `type_to_cpp` is qname-aware) -- a latent re-collision vector
    (see BUGS.md). THIR should make the qname the single carried identity so
    these scattered checks and the dual `to_cpp`/`type_to_cpp` rule collapse
    into one. Surfaced by `fix-cross-module-type-identity` (audit triage #13).
