# THIR Form-Dispatch Inventory

Ground-truth map of where today's codegen reconstructs **borrow-form vs
storage-form** and inserts conversions, produced to bootstrap the THIR form
design (`docs/IR_DESIGN.md` Open Questions 9, 11, 12). It is a *spec of what
THIR must subsume*, not a design.

Line numbers are indicative (grep the named functions/sets/helpers -- those are
stable). Generated 2026-06 by a parallel codegen sweep.

## Scope reminder: byte-identical, not optimal

The THIR form goal is to make codegen's **current, eager** form choices an
**explicit carried fact** (a form tag on the type + explicit conversion nodes),
byte-identical to today. *Optimizing* the choice (late representation + mem2reg
fold, Open Q 11) is **MIR-era**, not this. So this inventory catalogs the form
machinery to reproduce, not to improve.

## Headline finding

- **Conversion is already semi-centralized.** A `CppForm` enum (`BORROW` /
  `STORAGE`) and a single chokepoint `CodeGenContext.convert(val, dst_type,
  dst_form)` (`tpyc/codegen_cpp/context.py` ~2008-2051) already route most
  borrow<->storage bridging through the runtime helpers. `FormValue` carries an
  expr's current form. **This is the prototype of the THIR `FormConvert` node** --
  the design should generalize it, not invent it.
- **Detection is distributed.** *Which* form a site needs is re-derived per-site
  from ~12 predicates + ~22 local side-sets + the `RefType` wrapper. **This is
  what the form fact replaces:** a tag carried on the type/node so consumers read
  one fact instead of re-deriving.

So the design is: lift `CppForm` from a codegen-local notion to a THIR type/node
fact; emit `convert()` as an explicit `THIRFormConvert` node at lowering; delete
the detection side-sets in favor of the carried tag.

---

## 1. Form taxonomy -- the type families and their two forms

| Type family | Borrow form (params/locals/returns/frame) | Storage form (fields/containers/Own) | Key predicate |
|---|---|---|---|
| Tuple | `std::tuple<T*, ...>` (one pointer shape/elem) | `std::tuple<std::optional<T>, ...>` / `std::tuple<T, ...>` | `TupleType.has_pointer_repr_element()` |
| Optional `T \| None` (T ref) | `T*` | `std::optional<T>` | `OptionalType.uses_pointer_repr()` |
| Union `A \| B` (non-value) | `std::variant<A*, B*>` | `std::variant<A, B>` | `context.is_ptr_variant_union(t)` |
| Non-value local (record/list/dict/set) | `T*` (pointer-local) or `T&` (alias) | `T` (inline) | `is_value_type()` + side-sets |
| `str` | `std::string_view` (param/borrow) | `std::string` | TypeDef `param_cpp_formatter` |
| `String` | `const std::string&` | `std::string` | TypeDef `param_cpp_formatter` |
| `bytes` | `std::span<const uint8_t>` | `std::vector<uint8_t>` | TypeDef `param_cpp_formatter` |
| generic `T` (RefType `U`) | `val_or_ptr_t<T>` / `val_or_ref_t<T>` | `val_or_ref<T>` (stored) | `RefType` + traits |

`str`/`String`/`bytes`/`bytearray` are value types with a storage/param split
(Open Q 8) -- the generic ABI can't tell `str` from `String` (both `std::string`)
because the trait keys on the C++ storage type. Folded in here because the form
fact subsumes the split (Open Q 11 "scope extension").

---

## 2. The conversion surface (= the `THIRFormConvert` node kinds)

### 2a. The codegen chokepoint (prototype of the IR node)
- `CodeGenContext.convert(val, dst_type, dst_form)` -- `context.py` ~2008-2051.
  Single door for borrow<->storage; dispatches per type family to the helpers
  below. Takes a `FormValue` (expr + current `CppForm` + const-ness).
- `CppForm` enum (`BORROW`/`STORAGE`); `FormValue`; `source_form(expr)`
  (`context.py` ~1965-2006) classifies a leaf expr borrow/storage/value.

### 2b. Runtime bridge helpers (catalog -- each is a conversion kind)
**Optional** (`runtime/cpp/include/tpy/format.hpp` ~744-790):
`ptr_to_optional`, `ptr_to_optional_move`, `optional_to_ptr` (mut + const).

**Union** (`runtime/cpp/include/tpy/variant_ref.hpp` ~81-146):
`to_ptr_variant`, `to_const_ptr_variant`, `to_value_variant`.

**Tuple** (`format.hpp` ~801-1047):
`tuple_to_storage`(`_move`), `tuple_to_pointer`, `tuple_value_to_borrow`; per-elem
dispatchers `detail::to_storage_elem`(`_move`), `detail::to_pointer_form`;
comparison-across-forms `tuple_eq`/`tuple_lt`/`tuple_elem_eq`/`tuple_cmp_ref`.

**Generic slot** (`runtime/cpp/include/tpy/type_traits.hpp` ~174-238):
`val_or_ref_t`/`val_or_cref_t`/`param_val_or_ref_t`, `val_or_ptr_t`/`val_or_cptr_t`,
`tuple_elem_ref` (read generic slot), `to_val_or_ptr` (build generic slot).

**Iterator unwrap** (`runtime/cpp/include/tpy/next_iter.hpp` ~23-52):
`unwrap_ref`, `unwrap_ref_move`, `unwrap_tuple_refs`.

---

## 3. The detection surface (= what the form tag dissolves)

### 3a. Predicates (re-derive form per-site)
- `is_storage_form_source(expr)`, `is_const_storage_source(expr)`,
  `source_form(expr)`, `local_cpp_form(name)` -- `context.py` ~1905-2143.
- `OptionalType.uses_pointer_repr()`; `is_ptr_variant_union(t)` /
  `is_ptr_variant_source(expr)` (`context.py` ~1260-1305);
  `TupleType.has_pointer_repr_element()` / `_element_is_pointer_repr`;
  `_tuple_subscript_yields_borrow_ptr` (`expressions.py` ~5991).
- Per-binding "needs lift" gates: `needs_optional_to_ptr_lift`,
  `needs_to_ptr_variant_lift`, `callee_returns_own_ptr_optional`,
  `is_own_ptr_variant_param` (`context.py` ~2053-2233).

### 3b. `LocalCppForm` + the ~22 local side-sets (the bulk of what dissolves)
`LocalCppForm` enum (`context.py` ~627): `VALUE`, `POINTER`, `OPTIONAL_STORAGE`,
`STORAGE_OPTIONAL`, `VALUE_VARIANT`, `PTR_VARIANT`, `STORAGE_TUPLE`,
`BORROW_TUPLE`, `OPTIONAL_BORROW_TUPLE`.

Side-sets on `CodeGenContext` / `LocalScope` that back the classifier (each is a
name-keyed set the binding site writes and consumers read):
`pointer_locals`, `const_indirect_locals`, `optional_locals`,
`storage_form_optional_locals` (+ `const_`), `ptr_variant_locals`,
`storage_form_tuple_locals` (+ `const_`), `borrow_form_tuple_locals` (+ `const_`),
`optional_borrow_tuple_locals` (+ `const_`), `movable_locals`, `rebind_slots`,
`plain_rebind_slots`, `frame_field_shadows`, `generator_optional_fields`,
`generator_frame_slot_locals`, `generator_borrow_form_loop_vars`,
`walrus_pointer_locals` (+ `const_`), `walrus_storage_tuple_locals`,
`persistent_rebind_slots`. Snapshotted across branch scopes via `LocalScopeSnap`.

Eager binding-site writers (where the shape is picked):
`_gen_var_decl_code` (`statements.py` ~1940), `_gen_named_expr`/walrus
(`expressions.py` ~6466), `loop_var_binding` + `register_loop_var_storage_form`
(`context.py` ~357 / ~2164), generator frame-field reg (`statements.py` ~3100),
pointer-local rebind (`statements.py` ~2018).

### 3c. The `RefType` wrapper (Open Q 12 -- dissolves entirely)
- Production via `make_ref` (~30 sites): params/returns normalization
  (`sema/analyzer.py`), field/subscript/iterator-elem access (`sema/expressions.py`,
  `sema/statements.py`), tuple-element wrap (`typesys.py` ~2135), lambda params/return.
- Strip via `unwrap_ref_type` (~245 hits, ~8:1 strip:produce): var_types
  construction, overload/operator matching, canonicalization
  (`to_owned_storage_form`/`to_bare_slot_form`, `sema/type_ops.py` ~84-99).
- Genuine consumers (the wrapper modulates emission, ~11 sites): `to_cpp_stored()`
  -> `val_or_ref<T>` (`typesys.py` ~2081); `is_ref_param()` rvalue-temp binding;
  generic `val_or_ref_t`/lambda `-> T&` return; `is_ref[i]` tuple-unpack flag.
- At THIR: `Ref[T]` -> the borrow form tag; the strip fabric disappears.

### 3d. The str/bytes view machinery (folds in)
`PendingStrType`/`PendingBytesType` + `ViewVarInfo` per-variable usage tracking
(`typesys.py` ~4413-4537); `str_vars`/`bytes_vars` registries + `str_source_borrows`
(`sema/context.py`); `mark_view_reassigned_from_owned` / `mark_view_augassign`
(`sema/local_deduction.py`); TypeDef `cpp_formatter` vs `param_cpp_formatter` vs
`param_mut_cpp_formatter` (`type_def_registry.py` ~748-810).

---

## 4. Consumer-site inventory (where conversions are inserted)

Organized by boundary kind; each becomes an explicit `THIRFormConvert` (or a
form-tagged node) at lowering.

### Read sites (storage -> borrow value, on access)
- Tuple subscript read (`_gen_subscript` tuple branch; raw ptr vs `tuple_elem_ref`
  vs `optional_to_ptr` lift); field/method on a tuple subscript (`->` vs `.` via
  `_tuple_subscript_yields_borrow_ptr`); unpack bind (`unwrap_ref(tuple_elem_ref(..))`).
- Narrowed-Optional deref `_maybe_unwrap_narrowed_optional` (12 call sites:
  method/field receivers, for-iterables, subscript receivers).
- Union match access via `VariantAccess` (auto-deref for ptr-variant).
- print/repr/hash (`T*` deref overloads in `format.hpp`); comparison
  (`tuple_eq`/`tuple_lt`; `in`-needle storage lift).

### Boundary write sites (borrow -> storage, on store)
- Field write / record ctor field-init (`tuple_to_storage`, `ptr_to_optional`,
  value-variant store); subscript element write + `_lift_to_element_storage`.
- Container insert; Own[tuple] param (`tuple_to_storage_move`).

### Boundary lift sites (storage/value -> borrow, on pass/return)
- Call-arg bridges: `_gen_optional_ptr_arg`, union-arg `to_ptr_variant`, tuple-arg
  `tuple_to_pointer` (`expressions.py` ~1112-1176, ~274-377).
- return/yield: `_ensure_optional_return_is_ptr`, tuple yield
  `convert(.., BORROW)` (`statements.py` ~710, ~1730, ~3740).
- await-arg lift; ternary-arm normalization (`_ptr_variant_branch`,
  `_ptr_optional_branch`); walrus borrow bridge.
- Tuple literal construction slots (`_tuple_literal_slot_info` +
  `to_val_or_ptr`/`tuple_value_to_borrow`, `expressions.py` ~5736-5950).

### Known current form-mismatch defects (BUGS.md)
Nested tuples (outer/inner form disagree); rvalue tuple-of-records into a
borrow-form slot; rvalue generic tuple element; generic `V|None` with `V=Ptr[T]`
double-pointer; bare-Optional yield storage->ptr bridge; union `match` capture
(value-variant capture vs pointer-variant subject); async/await union return;
`key=` lambda over a generic-element tuple (consumer-dictated form). These are
the acid tests: the form fact should make each a visible conversion node.

---

## 5. Implications for the THIR form design

1. **Form tag.** Add a `form: Borrow | Storage` fact (on the THIR type, or as a
   node tag) for the eight families in section 1. `CppForm` is the existing seed.
2. **`THIRFormConvert` node.** Generalize `convert()` into an explicit lowering
   node whose `kind` is one of the section-2b helpers. Every section-4 site emits
   one at lowering instead of codegen re-deriving it.
3. **Dissolve detection.** The section-3 predicates + the ~22 side-sets +
   `RefType` collapse into reading the carried tag. This is the bulk of the win
   (and the bulk of the risk -- the side-sets encode subtle const-ness / rebind /
   suspension / null-state facts that the tag must preserve to stay byte-identical).
4. **Minimal validating slice (the spike).** Smallest case exercising one
   borrow<->storage conversion: a reference-type field read into a borrow local
   (`x = b.field`, storage `T` -> borrow `T*`), or Optional[ref] param + return.
   Prove the tag + `THIRFormConvert` reproduce `convert()` byte-identically before
   committing to the full node set.
5. **Sequencing.** Optional[ref] (single helper pair) -> tuple (richest) ->
   union -> non-value locals -> str/bytes split. Behind the same byte-identical
   net, one family per increment.
6. **Not in THIR.** Late representation selection + mem2reg fold (Open Q 11) stays
   MIR. THIR reproduces the eager choice; it does not optimize it.
