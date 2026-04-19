# Type System Migration Plan

## Goal

Unify the type hierarchy around two shapes: **nominal** (identity = qualified name + type args, behavior from a registry) and **structural** (identity = operand shape, behavior derived from operands). Builtins, containers, primitives, records, protocols, and enums all become `NominalType` with behavior sourced from a single `TypeDef` registry. Wrappers (`Ptr`, `Own`, `Optional`, `Union`, `Tuple`, `Callable`, `Readonly`) stay structural.

## Current state (Phases A, B, C, D, E, F.1, F.2a, F.2b complete)

```
TpyType (frozen dataclass base)
  NominalType (name, type_args)
    -- all container subclasses removed in Phase B.
    -- all primitive subclasses removed in Phase D step 6; primitives are
       singletons like NominalType("Int32", (), _module_qname="tpy.Int32").
    -- RangeType, EnumType, IntEnumType removed in Phase E; enums become
       NominalType("<Name>", (), _module_qname="<module>.<Name>") with an
       EnumInfo payload attached to TypeDef.enum. `__main__` entry-point
       enums use "__main__.<Name>" as their synthesized qname.
  PtrType, OwnType, OptionalType, UnionType, TupleType, ReadonlyType, ...  (structural wrappers)
  CallableType(is_template: bool)                                          -- FnType merged in, Phase C done
```

TypeDef registry in `tpyc/type_def_registry.py` holds per-qname behavior
(`cpp_formatter`, `is_send`/`is_sync` as bool or callable, `element_of`,
`subscript_borrows`, `is_value_type`, `needs_explicit_element_target`,
and the category payloads `record: RecordInfo`, `protocol: ProtocolInfo`,
`enum: EnumInfo`). `attach_dynamic_type_def(qname, category, ...)` creates
or updates entries at sema-registration time; `clear_dynamic_type_defs()`
resets them between compilations (hooked into
`clear_all_compilation_state`). Conformance tests in
`tpyc/test_type_def_registry.py` pin the invariants: `PRIMITIVE_SNAPSHOT`
(primitives), `ENUM_SNAPSHOT` (enums).

Remaining problems:
- Parser still constructs structural types (PtrType, OptionalType, UnionType, ...)
  and resolves module-local primitive names directly -- Phase F.3 makes parsing
  purely syntactic with sema owning all resolution.
- `_value_type_record_names` / `_send_record_names` / `_sync_record_names`
  accumulators on `typesys.py` parallel the new TypeDef.record payload;
  a future pass could fold them onto TypeDef fields and drop the globals.

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

1. **Nominal identity is qname-based; `_module_qname` is not a semantic shortcut.** Post-Phase-D, every builtin singleton -- primitives (Int32, str, bool, ...) *and* builtin generics (list, dict, set, Array, Span, ...) -- has `_module_qname` set. Branching sema validation on "has `_module_qname`" to skip registry/arity checks is wrong: it lets bare generics like `list` (no type args, factory demands one) pass validation. `validate_type` originally took this shortcut during step 6 and had to be re-done to validate arity against the factory's `param_kinds` (`get_type_factory_param_kinds`). The durable rule: **validity and arity for a NominalType come from looking up its qname in the TypeDef registry / record registry / factory table, not from inspecting `_module_qname`**. The `is_module_type` property that encoded this distinction has been **deleted** -- its one remaining codegen-side use (skipping `using` declarations for aliases whose C++ emission bypasses the Python name) was replaced with an `_emits_own_cpp(nominal)` helper in `codegen_cpp/generator.py` that consults `TypeDef.cpp_formatter` / `TypeDef.is_compile_time_only` / `_native_cpp_names` directly.

2. **Never use `type(x) == type(y)` for nominal-kind dispatch.** Post-Phase-D every container, every primitive, every record, every enum-class is a `NominalType`. `type(list[Int32]) == type(set[Int32])` is True; they differ only in `.name` / `.qualified_name()`. Two sites in `sema/overloads.py` had this pattern (`type_matches_numeric` at the recursive container-match branch, and `_structural_match` on the first-pass overload path). After the subclass collapse, both fired for every container pair and matched only element types, so a `list[Int32]` argument could silently match a `set[Int32]` overload via element recursion. Both fixed with explicit `NominalType.name` comparison. The durable rule: **for nominal-kind dispatch use qname/category predicates (`is_list`, `is_dict`, `is_fixed_int_type`, ...) or compare `NominalType.name` explicitly**. `type()` equality is only still meaningful between structural classes (`OptionalType` vs `UnionType` vs `TupleType` vs `NominalType`), where the axis of distinction really is the Python class.

Concrete followups to make the invariants stick:

- **(DONE)** Unified `validate_type` in `sema/type_ops.py`: no more branch selector on `_module_qname`. The path tries `get_record_for_type` first (covers user records plus imported builtins like `list` that have stub-based RecordInfo), then `get_type_factory_param_kinds(typ.qualified_name())`, then a bare-name namespace fallback (`builtins.<name>` / `tpy.<name>` to cover `def f(x: list)` where the parser leaves the NominalType without `_module_qname`), then enum / forward-ref. This catches both imported-bare and unqualified-bare generics uniformly without reading `_module_qname` as a semantic gate.
- **(DONE)** Deleted `NominalType.is_module_type` entirely. The sema call sites flipped to `is_user_record` (where they meant "not a builtin and not a protocol"); the single codegen call site in `generator.py` replaced with `_emits_own_cpp(nominal)` that consults `TypeDef.cpp_formatter` / `TypeDef.is_compile_time_only` / `_native_cpp_names` -- the actual architectural axis the check was trying to express ("does this type's C++ emission bypass the Python name?"). Zero `is_module_type` references remain in code.
- **(DONE)** Parser post-processing now attaches `_module_qname` for every builtin-resolved NominalType, not just `@builtin_type` records. Previously the post-processing at `parse/parser.py::_parse_type_annotation` checked only `get_builtin_type_key` (which is defined for stub-based records like `TextIO` / `BinaryIO` but misses factory-defined generics like `list` / `dict` / `set` / `Array` / `Span`). Widened the check to `get_builtin_type_key OR get_type_factory_param_kinds(candidate_qname)`, so `def f(x: list)` now produces `NominalType("list", (), _module_qname="builtins.list")` -- matching CPython's "builtins is implicitly imported" semantics and making the import-vs-no-import variants of bare generics take the same sema path and emit the same diagnostic.
- **(DONE)** Fixed `_structural_match` in `sema/overloads.py` (same `type(x) == type(y)` anti-pattern as `type_matches_numeric`, different code path -- pass-1 overload resolution). Both now require matching `NominalType.name` before recursing into inner types.
- **(DONE)** Regression tests under `tests/cases/` and `tpyc/test_type_def_registry.py`:
  - `cases/list/error_list_no_type_args` -- `def f(x: list) -> None` (no import) rejected with "Generic record 'list' requires type arguments: list[T]".
  - `cases/list/error_list_no_type_args_imported` -- same with `from builtins import list` (exercises both paths after the parser fix unified them).
  - `cases/calls/error_overload_cross_container` -- list[Int32] argument to set[Int32] | dict[...] overloads correctly fails.
  - `test_type_matches_numeric_rejects_cross_container` and `test_structural_match_rejects_cross_container` -- unit-level pins for the two overloads.py sites.
- **Durable review check** -- grep future PRs for `isinstance(x, NominalType) and not x.is_protocol` -- most sites want `x.is_user_record` instead (user records are the only ones that bypass the TypeDef/factory system).

### Phase E -- Records, protocols, enums (DONE)

Six sub-steps shipped on `typesys-migration-phase-e`:

1. **RangeType elimination.** `RangeType` collapsed into `NominalType("Range", (elem,), _module_qname="builtins.Range")` via a new `make_range` factory; `cpp_formatter=lambda args: f"::tpy::Range<{args[0].to_cpp()}>"` added to the `builtins.Range` TypeDef so `_emits_own_cpp` returns True for imported Range aliases. Zero call sites post-collapse (no code was reading `.elem`).
2. **Record/protocol TypeDef bookkeeping.** `TypeDef` is now non-frozen and carries `record: Optional[RecordInfo]` and `protocol: Optional[ProtocolInfo]` payloads. New `attach_dynamic_type_def(qname, category, *, record, protocol, enum, is_value_type)` creates-or-updates a TypeDef; `clear_dynamic_type_defs()` resets the dynamic slice and is hooked into `clear_all_compilation_state()`. `sema/registration.py` attaches on `register_record` (for `builtin_type_key` records and for user records in public modules -- `__main__` stays un-attached, same as the existing enum convention) and `register_protocol`. Accessors `record_info_of(t)` / `protocol_info_of(t)` added but have no consumers yet; the groundwork lets Phase F unify `@native` / `_native_cpp_names` / `@builtin_type` through a single TypeDef path.
3. **ENUM\_SNAPSHOT conformance table.** Hand-written golden values pin `is_int_enum` / `members` / `member_values` / `underlying_type` qname / `qualified_name` / `is_value_type` / `is_send` / `is_sync` / `to_cpp` / `member_value_map` for four fixtures (Enum/IntEnum × qualified/__main__/UInt8-underlying). Mirrors `PRIMITIVE_SNAPSHOT`'s role from Phase D.
4. **`EnumInfo` + `TypeDef.enum`.** `EnumInfo(members, member_values, underlying_type, is_int_enum, module_name)` added to `tpyc/type_def_registry.py`; `TypeDef.enum` is the payload slot. Accessor `enum_info_of(t)` and predicates `is_enum_type(t)` / `is_int_enum_type(t)` added. Both predicates initially kept isinstance fallbacks (removed in step 7).
5. **Enum TypeDef population.** `sema/registration.py::register_enum` now always attaches a TypeDef.enum entry, using `"__main__.<name>"` as the qname fallback for entry-point enums so `type_def_of(t)` resolves uniformly. `is_value_type=True` is set at attach time. The NominalType built for the enum carries `_module_qname` from the same synthesized qname.
6. **Reader flip.** ~49 `isinstance(x, EnumType)` / `isinstance(x, IntEnumType)` / `isinstance(x, (EnumType, IntEnumType))` sites across sema (match, expressions, statements, calls, protocols, analyzer) and codegen (generator, records, types, expressions, match) and `macro_api.py` converted to predicate calls. All `.members` / `.member_values` / `.underlying_type` / `.member_value_map` accesses on EnumType instances migrated to `enum_info_of(x).members` etc. The `_enum_lookup` helper in `sema/statements.py` simplified (the `isinstance(EnumType)` branch became dead once the subclass started being just a NominalType).
7. **Subclass deletion.** `EnumType` / `IntEnumType` removed from `typesys.py`. Parser and `sema/registration.py` construction sites emit `NominalType(name, (), _module_qname=...)` instead. `TypeRegistry.register_enum` / `get_enum` annotations retyped to `NominalType`. Two downstream fixes surfaced during deletion:
   - **Enum substitution in `TypeOperations.resolve_type`.** Parser emits `NominalType("Color", _module_qname=None)` for a same-module enum annotation; sema's `resolve_type` now substitutes it with the registered enum NominalType (which carries `_module_qname`), so `type_def_of(t)` resolves in downstream sema/codegen. Ordering also flipped: enums are registered before records so `register_record`'s field-type resolution can see them.
   - **`OptionalType.force_pointer_repr` staleness.** `resolve_type`'s `OptionalType` branch used to snapshot `typ.uses_pointer_repr()` on the *unresolved* inner, freezing the wrong decision when the inner later substituted into a value-typed enum. Now preserves only the original explicit `force_pointer_repr` (reserved for generic substitution) and lets `uses_pointer_repr()` recompute from the resolved inner.

Full suite green at every sub-step; `--force-exec` at the phase boundary also green (2606 passed, 1 skipped).

### Phase F.1 -- is_user_record via TypeDef (DONE)

Landed the Post-Phase-D invariant #1 cleanup for user records. Previously `NominalType.is_user_record` was defined as `not is_protocol and not _module_qname` -- exactly the "qname-as-shortcut" anti-pattern the invariants call out. With user records now carrying `_module_qname` and a TypeDef.record payload (populated in Phase E), the redefinition can consult the registry directly.

Changes:

1. **`RecordInfo.module`** added. Populated at `sema/registration.py::register_record` via `public_module_name(...)`. `RecordInfo.qualified_name()` derives `{module}.{name}` (or `__main__.{name}` for entry-point records); `builtin_type_key` takes precedence when set. Matches the convention already used by enums (Phase E).
2. **`NominalType.is_user_record` redefined** -- `TypeDef.record is not None and record.builtin_type_key is None` (protocols still filter out separately). Bare parser placeholders (no TypeDef entry) now return False; the old `_module_qname == None` semantic is gone.
3. **`resolve_type` user-record substitution** added (mirrors the enum substitution from Phase E): bare `NominalType("Counter", ...)` gets minted as `NominalType("Counter", ..., _module_qname="__main__.Counter")` so downstream `type_def_of(t)` resolves. Covers same-module and cross-module references uniformly.
4. **`resolve_type` TupleType recursion** added -- previously tuples were returned unchanged, so `tuple[Point, Point]` kept `Point` as a bare placeholder. Now walks element types.
5. **Constructor call result type** (`sema/calls.py::_analyze_record_constructor`) -- all return paths now mint qname-bearing NominalType. Covers non-generic (`Dog()`), generic-with-inferred-args (`Stack(1)`), and generic-with-explicit-args (`Stack[Int32]()`).
6. **Self type construction** (`build_record_self_type`) -- takes optional `qname` parameter; sema passes `RecordInfo.qualified_name()` so method bodies see a qname-bearing `Self`. Similarly for TypedDict construction, exception bindings, record-pattern patterns.
7. **Stub registration in `register_record`** -- for non-@builtin_type user records, a minimal `RecordInfo(name, module, type_params, type_param_kinds)` is stashed into `registry.records[name]` at the top of `register_record`, before field/method type resolution runs. This lets `resolve_type`'s user-record substitution find the record while its own full info is still being built. The full info (line 794+) overwrites the stub. `@builtin_type` stubs are skipped because the parser has already registered them with `builtin_type_key` and overwriting would strip parser-contributed state that `validate_type`'s method-signature arity check relies on.
8. **Parent NominalType minting** (`validate_record_inheritance`) -- record_info.parent gets a qname-bearing copy so downstream `is_user_record` checks on `record.parent` resolve.
9. **Codegen placeholder-tolerant helper** (`codegen_cpp/protocols.py::_is_user_record_name`) -- `collect_record_types_from_type` and `collect_type_args_of_bounded_records` operate on raw `TpyProtocol.methods` (AST), not on sema's resolved `ProtocolInfo.methods`. Those types stay qname-less. Helper falls back to `registry.get_record(name).builtin_type_key is None` when `is_user_record` returns False for lack of a TypeDef entry.
10. **NominalType.get_element_type category dispatch** -- old code defaulted to "first type_arg is element" whenever `_module_qname` was set. Post-migration user records have qnames too, so the check flipped to `TypeDef.category in {LIST, DICT, SET, ARRAY, SPAN, ITERATOR, DICT_VIEW, RANGE}`. Prevents `Tagged[Greeter]` from claiming its first type_arg (`Greeter`) as a container element.
11. **Placeholder-inclusive call sites fixed** (8 sites) -- replaced `is_user_record` filter (which used to coincidentally match bare placeholders) with explicit placeholder shape checks or direct `name in recursive_union_names` / `get_type_alias` lookups. Covers recursive-union expansion in compatibility / match / narrowing / statements / analyzer / typesys / codegen-expressions and parser placeholder-to-qname patching.

Full suite green at 2607 passed + 1 skipped; `--force-exec` at the phase boundary also green with byte-identical generated C++ across all cases.

### Phase F.2a -- `_resolve_imported_enums` removed (DONE)

Audit mapped every writer of AST type-annotation fields:

| AST field                               | Writer                                  |
| --------------------------------------- | --------------------------------------- |
| `TpyRecord.fields[i].type`              | `register_record` (registration.py:330) |
| `TpyRecord.methods[i].return_type/.params` | `_analyze_record_methods` (analyzer.py) |
| `TpyFunction.return_type/.params` (non-stub, top-level) | `_analyze_function` (analyzer.py) |
| `TpyFunction.return_type/.params` (@overload stubs) | `register_overload_group` (registration.py) |
| `TpyVarDecl.type` (top-level)           | `_analyze_var_decl` (statements.py:2125) |

Each writer calls `TypeOperations.resolve_type`, which already substitutes parser-level enum placeholders via the registry (added in Phase E). Only gap was **plain stub functions at module level** -- `register_function` resolved types into `FunctionInfo` but never wrote them back to the AST, so codegen would see a bare `NominalType("Color")` with no `_module_qname` for an imported enum referenced by a `@native` / stub signature. (No existing test case exercised this, but the gap was real.)

Changes:

1. **Writeback in `register_function`** (registration.py). Mirrors the pattern already used by `register_record` (field types) and `register_overload_group` (overload stub params/return). At the end of function registration, `func.params = list(resolved_params)` and `func.return_type = resolved_return`. For non-stubs, `_analyze_function` re-resolves and wraps with `make_ref`, overwriting this; for stubs, the resolved types persist and codegen sees qname-bearing NominalTypes.
2. **Deleted `_resolve_imported_enums`**, its caller at analyzer.py:391, and the three helpers (`_resolve_func_enums`, `_resolve_enum`). The `self.ctx.user_imported_enums` dict itself was initially kept (codegen still read it for C++ namespace qualification) but was later deleted in Phase F.2b along with its sibling dicts.

Full suite green: 2608 passed + 1 skipped with `--force-exec` (byte-identical generated C++).

### Phase F.2b -- Codegen qualifies types to canonical module (DONE)

Guiding principle from the user on the re-framing: "there should be one source of truth -- where a type is really defined. Import names are just aliases that should carry the real resolved true identity." In other words, codegen should never qualify a type through an import-alias path -- the canonical qname (declaring module) is the only identity we emit.

**What changed.** Codegen record/enum qualification now reads the canonical `(RecordInfo.defining_module, RecordInfo.name)` / `(EnumInfo.module_name, NominalType.name)` pair via two new registry helpers (`TypeRegistry.imported_record_qualification`, `imported_enum_qualification`). The three dicts on `codegen_cpp/CodeGenContext` (`user_imported_records`, `user_imported_enums`, `user_imported_type_aliases`) are gone. Sites migrated:

1. `codegen_cpp/types.py::type_to_cpp` -- records (was :324) and enums (was :336).
2. `codegen_cpp/expressions.py` -- record constructor (was :2208), enum type-level member access (was :2743), nested dotted access for records/enums (was :2754-2759).
3. `codegen_cpp/generator.py::generate` -- the loops that populate `_native_cpp_names` for imported records/enums now iterate the full `registry.records` / `registry.enums` and filter through the helpers. This fixes any `NominalType.to_cpp()` path that previously returned the alias form.

**Snapshot changes.** 43 expected files changed, purely canonicalization:
- `::tpystd::tplib::ArrayList` -> `::tpystd::tplib::array_list::ArrayList`
- `::tpystd::tplib::Box` -> `::tpystd::tplib::box::Box`
- `::tpystd::tplib::FixStr` -> `::tpystd::tplib::fix_str::FixStr`
- `::tpystd::tplib::json::JsonToken/Reader/Error` -> `::tpystd::tplib::json::parser::JsonToken/Reader/Error`

Both forms still compile: facade headers (`tplib.hpp`, `tplib.json.hpp`) already emit `using ::tpystd::tplib::array_list::ArrayList;` etc. for re-exports, so the short form continues to work where any is emitted -- but codegen no longer emits it.

**Followup that landed in the same phase.** After the codegen migration, the sema-side dicts (`user_imported_records` / `user_imported_enums` / `user_imported_type_aliases` on `SemanticContext`) were *also* deleted. The original obstacle -- that `RecordInfo.module` collapses private submodules (`tpy._builtins._bytes` -> `tpy`) while re-export needs the actual submodule -- was solved by adding a second field:

- `RecordInfo.module` -- public collapsed module, used for `qualified_name()` and canonical identity.
- `RecordInfo.defining_module` -- raw uncollapsed module where the class was declared, used by re-export lookup and codegen namespace qualification.

Both resolve to the same C++ namespace via `_namespace_map` / `cpp_namespace` directives, so swapping codegen from `module` to `defining_module` stayed byte-identical. Enums already had the right form on `EnumInfo.module_name` (always uncollapsed), and the alias dict moved onto `TypeRegistry.imported_type_alias_info` (alias bodies don't carry provenance so it has to be stored separately).

- `compiler.py::_exports_to_module_info` re-export loops now iterate `registry.records` / `registry.enums` directly, using `imported_record_qualification` / `imported_enum_qualification` to filter cross-module entries, plus a dedupe filter for aliased imports (`from X import P as MyP` registers both `MyP` and `P`; only the alias re-exports).
- The two codegen sites that used `user_imported_type_aliases` (union-alias registration, `using`-declaration emission in `codegen_cpp/generator.py`) now read `registry.imported_type_alias_info`.

Full suite green at 2608 passed + 1 skipped with `--force-exec`; no snapshot diffs beyond the canonicalization batch from the main F.2b phase.

**Regression tests** (added after a review found the aliased-re-export paths uncovered):
- `cases/imports/package_alias_reexport_record` -- `pkg/__init__.py` does `from .sub import Point as P`; main uses `from pkg import P`. Pins the record branch of the aliased-dedupe filter in `_exports_to_module_info`.
- `cases/imports/package_alias_reexport_enum` -- mirror of the above for an aliased enum re-exported through `__init__.py`.
- `cases/imports/package_alias_reexport_class_shadow` -- `__init__.py` has `from .sub import Foo` followed by a local `class Foo:`; verifies the local class shadows the imported one in pkg's exports. Guards the removal of `user_imported_records.pop(info.name)` in `registration.py`.

### Phase F.3 -- Parser emits unresolved type-reference nodes (NOT STARTED)

**Goal.** Make parsing purely syntactic: parser never constructs a `TpyType`.
All name resolution (primitives, builtin generics, user records/protocols/
enums, type parameters, import aliases) moves to sema. Parser imports from
`typesys` / `modules` drop to zero (or near-zero -- only `TypeRegistry` for
registration). Structural wrappers (`Ptr`, `Own`, `Optional`, `Union`,
`Tuple`, `Callable`, `Readonly`) are still represented structurally but via
`TpyTypeRef` wrapping in the AST, not by direct `PtrType(...)` construction
at parse time.

**Why now.** Post-F.2b, sema already re-resolves everything the parser
emits: `TypeOperations.resolve_type` substitutes enum placeholders, user-
record qnames, protocol flags, `TypeParamRef`s, and walks structural
wrappers. The parser's type construction is effectively a "first draft"
that sema rewrites. F.3 deletes the first draft and has parser emit the
unresolved form directly, collapsing two near-parallel resolution paths
into one.

**High-level steps** (original plan):

1. Introduce a pure AST node `TpyTypeRef(name, args, source_loc)` carrying
   unresolved type syntax. `args` is a tuple of `TpyTypeRef | int` (ints
   for `Array[T, N]` style).
2. Parser emits `TpyTypeRef` everywhere it currently constructs a
   `TpyType`. Parser no longer imports from `typesys` (at most imports
   `TypeRegistry` for registration, and a few structural helpers if
   unavoidable).
3. Sema adds a `resolve_type_ref(ref) -> TpyType` pass that walks
   `TpyTypeRef` against:
   - the TypeDef registry (builtins + user-registered records/protocols/
     enums);
   - local type parameters in scope;
   - structural syntax (`Ptr[...]`, `Own[...]`, `Optional[...]`, `T | U`,
     `tuple[...]`, `Callable[...]`, etc.) -- these build `StructuralType`
     directly.
4. Parser's local-name resolution for type annotations moves to sema,
   unifying with cross-module resolution (CLAUDE.md notes cross-module
   resolution is already deferred; this extends the pattern).
5. Type factory table in `modules/type_resolution.py` is deleted -- no
   longer called, since parser never constructs typed instances.

This phase is orthogonal to the nominal/structural split. It *could* run
before Phase A, but is cleaner after A (fewer type classes to shuffle) and
can run in parallel with Phases B-E once A is done -- the parser->sema
contract change is independent of subclass elimination.

**Sub-step breakdown.** Each sub-step ships independently, keeps the full
suite green, and preserves byte-identical generated C++. Pattern mirrors
F.1/F.2a/F.2b.

#### Phase F.3a -- Introduce `TpyTypeRef` (plumbing only)

Pure plumbing. No consumers yet.

1. Add `TpyTypeRef(name, args, source_loc)` to `parse/nodes.py`. `name` is
   the raw identifier as it appears in source (`"Int32"`, `"list"`,
   `"Ptr"`, `"Optional"`, `"T"`, `"Outer.Inner"`); `args` is a tuple of
   `TpyTypeRef | int`. No resolution semantics encoded in the node.
2. Decide whether `TpyTypeRef` represents *all* type syntax (including
   structural wrappers like `Ptr`/`Optional`/union) or whether a few
   special-cased structural node kinds coexist. **Tentative design**:
   one uniform node. `Ptr[T]` parses to `TpyTypeRef("Ptr", (TpyTypeRef("T",
   (), loc),), loc)`; `T | U` parses to `TpyTypeRef("|", (<ref T>, <ref
   U>), loc)` (or a dedicated `TpyUnionRef` — decide during implementation
   against readability).
3. No parser or sema wiring yet. This sub-step exists to lock in the node
   shape before anyone writes against it.

#### Phase F.3b -- Parser emits `TpyTypeRef` for leaf annotation sites

First real cut-over. Pick the annotation sites where sema's writers are
well-audited (from F.2a's table): function params/returns, var decls,
record fields, method signatures.

1. Extend sema `TypeOperations` with `resolve_type_ref(ref: TpyTypeRef) ->
   TpyType`. Initially this wraps the logic currently in parser's
   `_parse_type_annotation` (primitive resolution, registry lookup,
   structural-wrapper construction, type-param substitution, enum/record
   substitution). The existing `resolve_type` becomes a thin shim that
   dispatches on `TpyType` vs `TpyTypeRef`.
2. Switch parser's `_parse_type_annotation` entry to emit `TpyTypeRef` for
   the targeted AST fields. All other callers of `_parse_type_annotation`
   (generic function calls, isinstance checks, etc.) continue to receive
   `TpyType` for now.
3. Update AST field types (`TpyFunction.params`, `.return_type`;
   `TpyRecord.fields[i].type`; `TpyVarDecl.type`; method signatures) to
   `TpyTypeRef`. The F.2a writer table already catalogs which sema sites
   consume these.
4. Sema writers call `resolve_type_ref` instead of `resolve_type` for the
   migrated fields.
5. Verify byte-identical codegen + full suite green.

#### Phase F.3c -- Expand `TpyTypeRef` coverage to all annotation sites

Migrate the remaining annotation consumers:

1. `isinstance` checks (`TpyCall.isinstance_type`) and user-defined
   `isinstance` type args.
2. Generic function call type args (`func[Int32, str](...)`).
3. Class base lists and protocol inheritance
   (`TpyRecord.bases`, `TpyProtocol.bases`).
4. Type alias bodies (`TpyModule.type_aliases`).
5. Exception handler types (`TpyExceptHandler.type`).
6. With-item enter types (`TpyWithItem.enter_type`).
7. Match patterns (record-pattern type names, class-pattern type args).
8. `TpyTypeParamConstruct` bounds and defaults.

After this step, the AST should contain `TpyType` only for sema-resolved
results (never for parser output). Verify byte-identical codegen.

#### Phase F.3d -- Move primitive / builtin name resolution into sema

Parser drops its imports of `INT32`, `BOOL`, `STR`, `FLOAT`, ... and the
`qnames` module. `_resolve_primitive_type` and the related resolution
helpers delete; sema's `resolve_type_ref` now handles the name->qname
lookup via the TypeDef registry. Parser imports from `typesys` reduce to:

- `TypeRegistry` (registration bookkeeping still lives there);
- maybe a couple of structural-validation helpers (readonly/union
  normalization) if cleanly unavoidable.

Ideally none. Verify byte-identical codegen.

#### Phase F.3e -- Delete the type factory table

With parser resolution gone, `modules/type_resolution.py`'s factory table
(`get_type_factory_param_kinds`, `lookup_generic_type`, etc.) has only
sema-side readers for arity validation. Fold what remains into the
TypeDef registry (arity becomes a TypeDef field, or derives from
`RecordInfo.type_params`). `validate_type`'s multi-branch fallback
collapses to a single TypeDef lookup. Delete the factory module.

Verify byte-identical codegen + full suite green. Phase F.3 complete.

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

- **(DONE)** `basic_slice` module mismatch resolved: `BASIC_SLICE.qualified_name()` now returns `"tpy.basic_slice"` matching the factory-table key. Flipped in `typesys.py` (singleton `_module_qname`), `type_def_registry.py` (TypeDef registration + `is_basic_slice_type` predicate), `qnames.py` (BASIC_SLICE constant), and `test_type_def_registry.py` (fixture keys).

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
