# Type System Migration Plan

## Goal

Unify the type hierarchy around two shapes: **nominal** (identity = qualified name + type args, behavior from a registry) and **structural** (identity = operand shape, behavior derived from operands). Builtins, containers, primitives, records, protocols, and enums all become `NominalType` with behavior sourced from a single `TypeDef` registry. Wrappers (`Ptr`, `Own`, `Optional`, `Union`, `Tuple`, `Callable`, `Readonly`) stay structural.

## Current state (Phases A, B, C, D complete)

```
TpyType (frozen dataclass base)
  NominalType (name, type_args)
    -- all container subclasses removed in Phase B; containers now flow through
       NominalType + TypeDef registry with qname-based dispatch.
    -- all primitive subclasses (FixedIntType, BoolType, StrType, CharType,
       FloatType, Float32Type, BigIntType, CharType, StringType, StrViewType,
       BytesType, ByteArrayType, BytesViewType, BasicSliceType, SliceType,
       FStrType) removed in Phase D step 6; primitives are singletons like
       NominalType("Int32", (), _module_qname="tpy.Int32").
  EnumType, IntEnumType                                        (distinct data model -- TODO Phase E)
  PtrType, OwnType, OptionalType, UnionType, TupleType, ReadonlyType, ...  (structural wrappers)
  CallableType(is_template: bool)                                          -- FnType merged in, Phase C done
```

TypeDef registry in `tpyc/type_def_registry.py` holds per-qname behavior
(`cpp_formatter`, `is_send`/`is_sync` as bool or callable, `element_of`,
`subscript_borrows`, `is_value_type`, `needs_explicit_element_target`).
Conformance tests in `tpyc/test_type_def_registry.py` pin the invariants.

Remaining problems:
- Primitive subclasses hardcode names and C++ mappings; behavior is per-class instead of per-qname.
- Parser creates concrete subclass instances (via factory table) that should be deferred to sema.
- Two code paths for builtins vs user types throughout sema and codegen.

## Target state

```
TpyType (abstract)
  |
  +-- NominalType(qname, type_args)
  |       Everything with a name. Behavior from TypeDef registry.
  |       Covers: primitives (Int32, Bool, Str, Float, Char, ...),
  |       containers (list, dict, set, Span, Array, iterators, dict views),
  |       records, protocols, enums.
  |
  +-- StructuralType (abstract)
          Identity = shape of operands. No qname.
          |
          +-- PtrType(pointee)
          +-- ReadonlyType(wrapped)
          +-- OwnType(target)
          +-- OptionalType(target)
          +-- UnionType(members)
          +-- TupleType(elements)
          +-- CallableType(params, return_type, is_template)   -- merged FnType
          +-- IntLiteralType(value), FloatLiteralType(value)
          +-- PendingStrType (inference placeholder)
```

A single `TypeDef` registry, keyed by qname, carries all per-type behavior:

```python
@dataclass
class TypeDef:
    qname: str
    category: TypeCategory     # INT | FLOAT | BOOL | CHAR | STR | CONTAINER | RECORD | PROTOCOL | ENUM | ...
    cpp_formatter: Callable[[tuple[TpyType | int, ...]], str]
    is_value_type: bool
    is_send: bool
    element_of: Callable[[TpyType], TpyType] | None = None
    subscript_borrows: bool = False
    # category-specific payloads:
    int_traits:   IntTraits   | None = None   # width, signedness, range
    float_traits: FloatTraits | None = None
    enum:         EnumInfo    | None = None   # members, underlying type
    record:       RecordInfo  | None = None   # fields, methods
```

Dispatch is qname-based: `type_def_of(t).subscript_borrows`, `type_def_of(t).category == TypeCategory.FIXED_INT`. No `isinstance(x, ListType)`, no `isinstance(x, FixedIntType)`. `isinstance` survives only to split nominal from structural (and among structurals).

## Migration plan

Each phase is small enough to ship as one PR or a handful. Cases should produce byte-identical generated C++ at every phase boundary -- any exec/cpy rebuild during the refactor is a regression signal.

### Phase A -- Foundation (DONE)

1. Renamed `NamedType` -> `NominalType` (~527 call sites across 46 files).
2. Added `TypeDef` dataclass and `_type_defs: dict[str, TypeDef]` in `tpyc/type_def_registry.py`, populated for every builtin qname.
3. Added `type_def_of(t) -> TypeDef | None` and per-qname predicates (`is_list`, `is_dict`, `is_span`, `is_array`, `is_set`, `is_dict_view`, `is_range`, `is_span_iter`, `is_copy_iter`, `is_own_iter`, `is_iterator_adapter`).
4. Conformance tests in `tpyc/test_type_def_registry.py` pin TypeDef against subclass behavior.

### Phase B -- Container subclass elimination (DONE)

All 11 container subclasses deleted:
dict views (DictKeysViewType, DictValuesViewType, DictItemsViewType) ->
iterator adapters (SpanIterType, CopyIterType, OwnIterType) ->
SetType -> DictType -> ArrayType -> SpanType -> ListType.

Each type migrated in its own batch: TypeDef populated, `make_*` factory
added, call sites flipped (~10-100 per type), subclass deleted, full
suite green.

Infrastructure added in the process:
- `cpp_formatter` on TypeDef (for qnames whose C++ name diverges from
  Python, e.g. `list -> std::vector<T>`, `dict_keys -> ::tpy::dict_keys_view`).
- `is_send` / `is_sync` on TypeDef accept `bool | Callable[[type_args], bool]`
  (containers like list/dict/set have args-dependent Send).
- `element_of` on TypeDef for types whose iteration element is different
  from `type_args[0]` (SpanIter[readonly[T]] iterates T, not readonly[T]).
- `needs_explicit_element_target` for Array/Span literal initializers.
- `NominalType.inner_types` / `with_inner_types` skip integer-kind
  `TypeParamRef`s so Array[T, N: int] traversal doesn't treat N as element.
- `NominalType.is_send` / `is_sync` consult TypeDef *before* checking
  `_send/sync_record_names` -- builtin qname behavior dominates record-
  level registration (e.g. list's @native stub has no disqualifying fields
  and would be registered as Sync, but TypeDef says list is never Sync).
- Module-level helpers for Span const manipulation (`span_is_readonly`,
  `span_inner_element`, `span_as_const`, `span_as_mutable`) replacing the
  former `SpanType.as_const()` / `.is_readonly` properties.

Lessons learned (for later phases):
- Perl/sed bulk replacements are dangerous around tuple `isinstance`,
  type annotations, and anywhere the regex can eat surrounding context.
  Use `Grep` + targeted `Edit` for call-site flipping.
- Safe atomic patterns: `ClassName(arg)` -> `make_x(arg)`, single-arg
  `isinstance(ident, ClassName)` -> `is_x(ident)`. Everything else needs
  per-site context.

### Phase C -- Merge FnType and CallableType (DONE)

`FnType` deleted; `CallableType` gained `is_template: bool = False` field.
`make_fn_type(params, ret)` factory and `is_fn_type(t)` predicate added.
`TypeKind.FN` merged into `TypeKind.CALLABLE`.

Semantic gates that used to be `isinstance(hint, CallableType)` (implicit:
"Callable, not Fn") now check `isinstance(hint, CallableType) and not
hint.is_template`:
- Lambda capture mode (`captures_by_value` for Callable, by-ref for Fn).
- Nested def escape tracking.
- Compat + overload matching.

### Phase D -- Collapse primitives

**Step 0 -- Independent conformance snapshot (DONE).**
The Phase A conformance tests in `tpyc/test_type_def_registry.py` compared
TypeDef answers against subclass methods. Post-Phase B the container
subclasses are gone, so those asserts are tautological (both sides read
from the registry). Before starting Phase D's subclass deletion, a
hard-coded snapshot table `PRIMITIVE_SNAPSHOT` was added to
`test_type_def_registry.py` pinning intrinsic per-qname behavior
(`is_value_type`, `is_send`/`is_sync`, `subscript_borrows`, `is_expensive_copy`,
`param_needs_copy_for_reassign`, `is_compile_time_only`, `to_cpp`,
`to_cpp_param_type`, element qname, `int_bits`/`int_signed` for fixed
ints, `float_bits` for floats). The snapshot is compared against the
live `TpyType` instance. It must keep passing through every subclass
deletion -- the snapshot values are independent of the implementation
path that computes them.

Primitive subclasses (`FixedIntType`, `BoolType`, `StrType`, `CharType`, `FloatType`, per-width aliases) carry behavior: arithmetic rules, coercion, range, `to_cpp`. Move all of it to TypeDef:

1. **(DONE)** TypeDef enriched with `IntTraits(bits, signed)` + computed `min_value`/`max_value`, `FloatTraits(bits)`, plus `param_cpp_formatter`, `is_expensive_copy`, `param_needs_copy_for_reassign`, `is_compile_time_only`. Every primitive qname populated.
2. **(DONE)** Predicate helpers (`is_fixed_int_type` / `is_bool_type` / `is_char_type` / `is_str_type` / `is_string_type` / `is_str_view_type` / `is_fstr_type` / `is_float64_type` / `is_float32_type` / `is_big_int_type` / `is_bytes_type` / `is_bytearray_type` / `is_bytes_view_type` / `is_basic_slice_type` / `is_slice_type`) plus category predicates and `int_traits_of` / `float_traits_of` accessors added to the registry. `TpyType.type_args = ()` default on the base class so primitives expose the attribute without special-casing.
3. **(DONE)** All callers flipped from `isinstance(x, FixedIntType)` / `.bits` / `.signed` / `.min_value` / `.max_value` to the new predicates and trait accessors. Covers `coercions.py`, sema (numeric_lattice, overloads, compatibility, calls, context, expressions, registration, statements, methods, match, local_deduction, protocols), codegen (builtins, types, context, match, functions, protocols, gen_generators, statements, records, expressions), `macro_api.py`, `repl.py`, and a handful of sites inside `typesys.py` (`LiteralType`, `OptionalType` param rendering, `final_type_str_to_strview`, `is_any_bytes_type`, `FunctionInfo.has_fstr_param`). `type_def_of` guards non-TpyType input so predicates quietly return False on the `int` values that appear in `Array[T, N]`-style type_args. `_is_qn` / `_is_cat` exclude `LiteralType` and `PendingViewType` because those types delegate `qualified_name()` to an inner base -- mirrors the old `isinstance(x, PrimitiveSubclass)` semantics. Full suite green with byte-identical generated C++.
4. **(DONE)** Base `TpyType` methods (`to_cpp`, `to_cpp_param_type`, `to_cpp_param`, `to_cpp_const_param`, `is_value_type`, `is_send`, `is_sync`, `is_expensive_copy`, `param_needs_copy_for_reassign`, `is_compile_time_only`, `get_element_type`) now consult `TypeDef` first and only fall back to their historical defaults when no registry entry exists. `LiteralType` gets an explicit `get_element_type -> None` override so its "not a container" semantics survive the base-class upgrade. Added `element_of` for the str family (returns CHAR) and bytes family (returns UINT8) so post-delete NominalType instances still iterate correctly.
5. **(DONE)** Every primitive subclass trimmed to a marker: `@dataclass(frozen=True)`, `tag`, `__str__`, `qualified_name`. Behavior lives entirely in TypeDef. `FixedIntType` keeps `bits`/`signed` instance fields plus the `min_value`/`max_value` properties because each width is a distinct instance; those fields are unused outside `typesys.py` now (callers read `int_traits_of(...)`) but are retained because the subclass itself still exists.
6. **(DONE)** All primitive subclasses deleted. Singletons are now `NominalType(name, (), _module_qname=qname)` (e.g. `INT32 = NominalType("Int32", (), _module_qname="tpy.Int32")`, `STR = NominalType("str", (), _module_qname="builtins.str")`, `FSTR = NominalType("FStr", (), _module_qname="tpy.FStr")`). Done per-family in batches: FStr (proof point) -> basic_slice/slice -> bytes+str families -> char/bool/float -> BigInt -> FixedInt. Work triggered several adjacent cleanups that had been latent while subclasses still distinguished primitives: `_VIEW_TYPE_TO_FAMILY` (sema/statements) re-keyed by qname (was `type(x)`); `ViewTypeFamily.promote_param_type: type` -> `promote_param_match: Callable[[TpyType], bool]`; fstring `is_user_type` check in codegen switched to `is_user_record` so builtins no longer trigger `__str__` wrapping; `validate_type` in sema/type_ops validates module-type arity against the factory's param kinds (was initially a bypass -- see "Post-Phase-D invariants" below); imported-alias `using` declaration skips `is_module_type` to avoid spurious `using Float64;` etc.; parser decorator `_type_map` flipped from `type(x)` keys to predicate calls; coercions.py `Coercion.from_type` / `to_type` became predicate callables; `NominalType.to_cpp` honors `TypeDef.is_compile_time_only` (raises TypeError). `sema/overloads.py::type_matches_numeric` had a latent `type(arg) == type(param)` check that post-collapse matched across unrelated containers (list vs set vs dict) -- fixed to also compare `NominalType.name`. Two error-message diag snapshots updated (Int32 is now a record-shaped NominalType for formatting purposes). Full suite: 2595 passed, 1 skipped. Generated C++ byte-identical for all existing cases.
7. **(DONE)** `TypeKind` enum deleted along with every `tag: ClassVar[TypeKind]` declaration on `TpyType` and its structural subclasses (VoidType, NoneType, IntLiteralType, FloatLiteralType, LiteralType, OptionalType, UnionType, CallableType, PendingStrType). All readers had already been migrated in earlier steps — the tags were write-only dead code after step 6. `is_void_like_type` / `is_callable_type` / `is_union_or_optional_type` were converted to plain `isinstance` checks on the structural subclasses. Full suite green post-deletion.

### Phase D -- Cleanups discovered during step 6

- **(DONE)** Two test diag snapshots updated (cases/exceptions/error_raise_expr_non_exception, cases/imports/error_dotted_module_shadowed) because Int32 now hits the NominalType-record branches for error formatting. Both new messages are arguably clearer; no behavior change.
- **`pending_type_class`** in ViewTypeFamily remains a class (PendingStrType / PendingBytesType) rather than a predicate -- those are true structural subclasses with instance fields (`var_id`), so the `type(x)` dispatch is still correct. Left as-is.
- **(DONE)** Comment drift across `codegen_cpp/{statements,expressions}.py`, `sema/{calls,statements}.py`, `coercions.py`, `type_def_registry.py`, `typesys.py`: updated ~14 comments that referenced deleted class names (`StrType` / `StrViewType` / `BytesType` / `BytesViewType` / `BigIntType` as adjectives) to use the user-facing type names (`str`, `StrView`, `bytes`, `BytesView`, `int`).

### Post-Phase-D invariants (rules for Phases E/F/G and future contributors)

Two half-migrated assumptions slipped through Phase D and shipped regressions before being caught in review. Both are now fixed, but the rules below are worth spelling out so future work doesn't reintroduce them.

1. **Nominal identity is qname-based; `_module_qname` is a codegen hint, not a semantic shortcut.** Post-Phase-D, `is_module_type` (i.e. `_module_qname is not None and not is_protocol`) is True for every builtin singleton -- primitives (Int32, str, bool, ...) *and* builtin generics (list, dict, set, Array, Span, ...). Branching sema validation on `is_module_type` to skip registry/arity checks is wrong: it lets bare generics like `list` (no type args, factory demands one) pass validation. `validate_type` originally took this shortcut during step 6 and had to be re-done to validate arity against the factory's `param_kinds` (`get_type_factory_param_kinds`). The durable rule: **validity and arity for a NominalType come from looking up its qname in the TypeDef registry / record registry / factory table, not from inspecting `_module_qname`**. `is_module_type` is fine for codegen hooks (skip emitting `using` declarations for builtins, route `__str__` vs `__repr__` on user records only), but not for "is this annotation well-formed?"

2. **Never use `type(x) == type(y)` for nominal-kind dispatch.** Post-Phase-D every container, every primitive, every record, every enum-class is a `NominalType`. `type(list[Int32]) == type(set[Int32])` is True; they differ only in `.name` / `.qualified_name()`. Two sites in `sema/overloads.py` had this pattern (`type_matches_numeric` at the recursive container-match branch, and `_structural_match` on the first-pass overload path). After the subclass collapse, both fired for every container pair and matched only element types, so a `list[Int32]` argument could silently match a `set[Int32]` overload via element recursion. Both fixed with explicit `NominalType.name` comparison. The durable rule: **for nominal-kind dispatch use qname/category predicates (`is_list`, `is_dict`, `is_fixed_int_type`, ...) or compare `NominalType.name` explicitly**. `type()` equality is only still meaningful between structural classes (`OptionalType` vs `UnionType` vs `TupleType` vs `NominalType`), where the axis of distinction really is the Python class.

Concrete followups to make the invariants stick:

- **(DONE)** Unified `validate_type` in `sema/type_ops.py`: the `is_module_type` branch selector is gone. The path tries `get_record_for_type` first (covers user records plus imported builtins like `list` that have stub-based RecordInfo), then `get_type_factory_param_kinds(typ.qualified_name())`, then a bare-name namespace fallback (`builtins.<name>` / `tpy.<name>` to cover `def f(x: list)` where the parser leaves the NominalType without `_module_qname`), then enum / forward-ref. This catches both imported-bare and unqualified-bare generics uniformly without reading `_module_qname` as a semantic gate.
- **(DONE)** Parser post-processing now attaches `_module_qname` for every builtin-resolved NominalType, not just `@builtin_type` records. Previously the post-processing at `parse/parser.py::_parse_type_annotation` checked only `get_builtin_type_key` (which is defined for stub-based records like `TextIO` / `BinaryIO` but misses factory-defined generics like `list` / `dict` / `set` / `Array` / `Span`). Widened the check to `get_builtin_type_key OR get_type_factory_param_kinds(candidate_qname)`, so `def f(x: list)` now produces `NominalType("list", (), _module_qname="builtins.list")` -- matching CPython's "builtins is implicitly imported" semantics and making the import-vs-no-import variants of bare generics take the same sema path and emit the same diagnostic.
- **(DONE)** Fixed `_structural_match` in `sema/overloads.py` (same `type(x) == type(y)` anti-pattern as `type_matches_numeric`, different code path -- pass-1 overload resolution). Both now require matching `NominalType.name` before recursing into inner types.
- **(DONE)** Regression tests under `tests/cases/` and `tpyc/test_type_def_registry.py`:
  - `cases/list/error_list_no_type_args` -- `def f(x: list) -> None` (no import) rejected with "Generic record 'list' requires type arguments: list[T]".
  - `cases/list/error_list_no_type_args_imported` -- same with `from builtins import list` (exercises both paths after the parser fix unified them).
  - `cases/calls/error_overload_cross_container` -- list[Int32] argument to set[Int32] | dict[...] overloads correctly fails.
  - `test_type_matches_numeric_rejects_cross_container` and `test_structural_match_rejects_cross_container` -- unit-level pins for the two overloads.py sites.
- **Durable review check** -- grep future PRs for `isinstance(x, NominalType) and not x.is_protocol` -- most sites want `x.is_user_record` instead (user records are the only ones that bypass the TypeDef/factory system).

### Phase E -- Records, protocols, enums

Records and protocols already flow through `NominalType` today, so registering a TypeDef on record/protocol creation is mostly bookkeeping. Enums: move `EnumType` / `IntEnumType` data (members, underlying type) to `TypeDef.enum`; migrate enum-specific code to read from TypeDef; delete the enum subclasses.

### Phase F -- Move type resolution from parser to sema

Today the parser imports 20+ type classes from `typesys` and constructs `PtrType`, `OwnType`, `OptionalType`, `UnionType`, `TupleType`, `FnType`, `CallableType`, `ListType` (via factories), etc. directly. It also resolves local-name type annotations. This phase makes parsing purely syntactic -- parser emits unresolved type-reference AST nodes, sema owns all resolution.

Steps:

1. Introduce a pure AST node `TpyTypeRef(name, args, source_loc)` carrying unresolved type syntax. `args` is a tuple of `TpyTypeRef | int` (ints for `Array[T, N]` style).
2. Parser emits `TpyTypeRef` everywhere it currently constructs a `TpyType`. Parser no longer imports from `typesys` (at most imports `TypeRegistry` for registration, and a few structural helpers if unavoidable).
3. Sema adds a `resolve_type_ref(ref) -> TpyType` pass that walks `TpyTypeRef` against:
   - the TypeDef registry (builtins + user-registered records/protocols/enums);
   - local type parameters in scope;
   - structural syntax (`Ptr[...]`, `Own[...]`, `Optional[...]`, `T | U`, `tuple[...]`, `Callable[...]`, etc.) -- these build `StructuralType` directly.
4. Parser's local-name resolution for type annotations moves to sema, unifying with cross-module resolution (CLAUDE.md notes cross-module resolution is already deferred; this extends the pattern).
5. Type factory table in `modules/type_resolution.py` is deleted -- no longer called, since parser never constructs typed instances.

This phase is orthogonal to the nominal/structural split. It *could* run before Phase A, but is cleaner after A (fewer type classes to shuffle) and can run in parallel with Phases B-E once A is done -- the parser->sema contract change is independent of subclass elimination.

### Phase G -- Sema module cleanup (optional, independent)

Break circular imports by extracting shared types into leaf modules. Benefits from A-F (fewer cross-references) but doesn't depend on them. Candidates: call-analysis utilities out of `calls.py`, splitting `SemanticContext`.

## Safety techniques

- **Conformance tests** (Phase A step 4): the TypeDef registry is validated against the existing subclass hierarchy *before* anything is deleted. Catches "TypeDef forgot about behavior X" early. Once green, subclass removal is boring.
- **Byte-identical generated C++**: every phase should produce zero diffs in `expected/include/*.hpp` and `expected/src/*.cpp`. The fingerprint cache in `test_case` catches drift automatically -- any case whose exec phase doesn't skip during the refactor is worth investigating. Snapshot updates during this migration should be reviewed line-by-line, not rubber-stamped.
- **Per-type PRs in Phase B**: dict views (~9 sites) is a single small PR. Ship, verify, move on. Build confidence incrementally.
- **`--force-exec` at phase boundaries**: catches cases where generated code changed but fingerprints happened to collide.

## Design decisions

- **Nominal vs. structural** is the real axis, not "named vs. container vs. primitive." `NominalType` covers anything with a qname; `StructuralType` covers everything whose identity is operand shape.
- **TypeDef is one table, not many registries.** Scattering `_SUBSCRIPT_BORROWS_QNAMES`, `_IS_SEND_QNAMES`, etc. across modules scales poorly. One `TypeDef` entry per qname with rich fields makes it easy to discover all behavior of a type in one place, and adding a new builtin means touching one line.
- **Primitives collapse too.** Keeping `FixedIntType` as a subclass with a `.name` field is the hybrid trap. If behavior lives in TypeDef, the subclass has nothing left to justify its existence.
- **Enums collapse too.** Members and underlying type belong on `TypeDef.enum`, not on a separate subclass. Enums are nominal -- they have names.
- **Wrappers stay structural.** `Ptr`, `Own`, `Optional`, `Union`, `Tuple`, `Callable`, `Readonly` all have structural identity. No TypeDef.
- **`ReadonlyType` already exists** (typesys.py:1540) and is used correctly by `PtrType` (pointee is wrapped) and `SpanType` (first type_arg is wrapped). No representation change needed; the const-propagation logic in `SpanType.to_cpp` moves to the `Span` TypeDef's `cpp_formatter` during Phase B.
- **Literal types are structural**, not nominal. `IntLiteralType(value)` / `FloatLiteralType(value)` carry a value, not a qname.
- **`PtrType.is_readonly` convenience accessor stays.** It's a property that checks `isinstance(self.pointee, ReadonlyType)` -- harmless and readable. No need to force callers to unwrap manually.

## Followups (pre-existing cleanups surfaced during the migration)

- **`basic_slice` module mismatch**: `BasicSliceType.qualified_name()` returns `"builtins.basic_slice"` but the parser factory table in `tpyc/modules/type_resolution.py` keys it as `"tpy.basic_slice"`. `basic_slice` is a tpy-specific type (not a CPython standard), so `qualified_name()` should be `"tpy.basic_slice"` to match the factory. The TypeDef registry (`tpyc/type_def_registry.py`) currently follows the class; flip both to `"tpy.basic_slice"` when doing the fix.

## Performance tradeoffs introduced by Phase D

Phase D moved most type-dispatch from C-level `isinstance` checks to Python-level predicate calls (`is_fixed_int_type`, `is_str_type`, ...) and `TypeDef` dictionary lookups. The architectural win is real -- one place to add/change behavior per qname -- but the move has a per-call cost worth tracking, since compiler throughput matters for large projects.

**Known hot paths where the cost concentrates:**

- **`coercions.py:resolve_coercion`** -- linear scan over ~39 rules, each evaluating two predicates (`from_type(actual)` and `to_type(expected)`) plus an optional `type_match`. Pre-Phase-D this was one C-level `isinstance` per rule (roughly two machine instructions); now it's a Python function call plus a `type_def_of` dict lookup per rule (tens of instructions each). For a compilation with N coercion resolutions the overhead is roughly `N * 39 * 2 * (Python call + dict lookup)`. For typical tpyc workloads this is likely noise, but hasn't been measured.
- **`type_def_of`** on every `is_*_type` call. Each hit is `qualified_name()` + `dict.get`, called from many predicates. `qualified_name()` is an instance-method indirection that branches per subclass.
- **Tag-based predicates** (`is_numeric_type`, `is_primitive_type`, `is_any_str_type`, ...) formerly were `t.tag in _TAGS_FROZENSET` (one attribute + one set lookup). They now compose `is_fixed_int_type(t) or is_big_int_type(t) or ...` with up to four `type_def_of` lookups per call.

**Mitigations available if measurement shows a problem:**

1. **Hash-table dispatch in `resolve_coercion`**: when both sides are qname-resolvable, the rule can be indexed by `(from_qname, to_qname) -> Coercion`. Fallback to linear scan only for rules whose side is `_match_any_side`. Could cut per-call cost by >10x.
2. **Per-instance tag cache on `NominalType`**: store the `TypeDef.category` on the instance at construction (one `type_def_of` lookup per singleton, then O(1) attribute access thereafter). Would let tag-based predicates return to the original frozenset-lookup speed.
3. **mypyc**: the current shape (small pure predicates calling other predicates) is mypyc-friendly -- mypyc can inline predicate calls and specialize `type_def_of` dispatch. The Python-level overhead should mostly vanish under mypyc.
4. **Conservative profiling before optimizing**: the compiler isn't in a tight loop -- most work is I/O (reading source, writing C++) and semantic analysis that's dominated by other costs. Benchmark before assuming dispatch cost is the bottleneck.

No optimization work is planned now; this is a "remember the tradeoff exists" note for the day someone benchmarks `tpyc` on a large project and the dispatch cost shows up in a profile.
