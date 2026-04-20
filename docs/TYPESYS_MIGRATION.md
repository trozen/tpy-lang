# Type System Migration Plan

## Goal

Unify the type hierarchy around two shapes: **nominal** (identity = qualified name + type args, behavior from a registry) and **structural** (identity = operand shape, behavior derived from operands). Builtins, containers, primitives, records, protocols, and enums all become `NominalType` with behavior sourced from a single `TypeDef` registry. Wrappers (`Ptr`, `Own`, `Optional`, `Union`, `Tuple`, `Callable`, `Readonly`) stay structural.

## Current state (Phases A, B, C, D, E, F.1, F.2a, F.2b, F.3a-F.3g, F.4 complete)

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
the generic-instantiation payload `param_kinds: tuple[TypeParamKind, ...]`
+ `type_factory: Callable[..., TpyType] | None`, and the category payloads
`record: RecordInfo`, `protocol: ProtocolInfo`, `enum: EnumInfo`).
`attach_dynamic_type_def(qname, category, ...)` creates or updates
entries at sema-registration time; `clear_dynamic_type_defs()` resets
them between compilations (hooked into `clear_all_compilation_state`).
Conformance tests in `tpyc/test_type_def_registry.py` pin the
invariants: `PRIMITIVE_SNAPSHOT` (primitives), `ENUM_SNAPSHOT` (enums),
`FACTORY_SNAPSHOT` (generic-instantiation payload).

Remaining problems:
- Tracked as **Phase F.5** below: the three substitution blocks
  (enum / user-record / protocol-qname) in `sema/type_ops.py::resolve_type`
  survive because parser's `TypeResolver` can't see re-export facades'
  defining modules.

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

### Phase F.3 -- Parser emits unresolved type-reference nodes (F.3a-F.3g DONE)

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

#### Phase F.3a -- Introduce `TpyTypeRef` (plumbing only, DONE)

Pure plumbing. No consumers yet.

Landed a 4-node hybrid rather than the tentative uniform design. Three
Python type-syntax forms resist uniform `name + args` representation:

- `T | U` (BinOp, not subscript; variable-arity members).
- `Callable[[P1, P2], R]` / `Fn[[P1, P2], R]` (inner list-shape for the
  param group).
- `Literal[v1, v2, ...]` (args are values, not types).

Giving each a dedicated AST node makes `resolve_type_ref` dispatch
cleanly via `isinstance` (mirrors how sema already handles `PtrType` vs
`UnionType` vs `CallableType` vs `LiteralType`). No magic name strings
(`"__union__"`, `"|"`), no non-uniform argument encoding.

Nodes added in `parse/nodes.py`:

```python
@dataclass(frozen=True)
class TpyTypeRef:
    """Name or Name[args]. Covers primitives, generics, structural wrappers
    expressed via subscript (Ptr[T], Own[T], Optional[T], Readonly[T],
    tuple[T1,T2], Array[T, N], ...), qualified names (Outer.Inner), type
    parameters, Self, None."""
    name: str
    args: tuple[TypeRefNode | int, ...] = ()   # int for Array[T, N]
    loc: SourceLocation | None = None

@dataclass(frozen=True)
class TpyUnionRef:
    members: tuple[TypeRefNode, ...]
    loc: SourceLocation | None = None

@dataclass(frozen=True)
class TpyCallableRef:
    kind: Literal["Callable", "Fn"]
    params: tuple[TypeRefNode, ...]
    return_type: TypeRefNode
    loc: SourceLocation | None = None

@dataclass(frozen=True)
class TpyLiteralRef:
    values: tuple[LiteralValue, ...]           # reuses existing LiteralValue
    loc: SourceLocation | None = None

type TypeRefNode = TpyTypeRef | TpyUnionRef | TpyCallableRef | TpyLiteralRef
```

Notes:

- `frozen=True` signals "parser output, treat as immutable" and matches
  the convention used by `TpyType` subclasses.
- PEP 695 `type` alias (`type TypeRefNode = ...`) is used because it
  lazy-evaluates -- forward references inside the aliased classes work
  without string quoting.
- Dotted qualified names stay as a single `name` string
  (`"Outer.Inner"`); sema splits them during resolution.
- All four nodes re-exported from `parse/__init__.py`.
- No parser or sema wiring yet. F.3b introduces the first consumers.

#### Phase F.3b -- Parser emits `TpyTypeRef` for leaf annotation sites

First real cut-over. Pick the annotation sites where sema's writers are
well-audited (from F.2a's table): function params/returns, var decls,
record fields, method signatures.

Split into three sub-sub-steps because the resolution state that
`resolve_type_ref` needs (parser's `_local_defs`, `_module_class_names`,
`_module_type_alias_names`, `_nested_type_scope`, `_pending_alias_name`,
`_reverse_module_aliases`, plus `_imports`) is currently parser-private.
Moving it all at once couples "introduce the refactored walker" with
"move state out of parser with ownership changes," which is too risky
for one commit. Each sub-sub-step ships independently with full suite
green + byte-identical generated C++.

##### F.3b.1 -- Standalone syntactic walker (DONE)

Adds `Parser._parse_type_ref(ast_expr, type_param_scope) -> TypeRefNode`
in `tpyc/parse/parser.py`. Pure syntactic walk: builds TypeRefNode from
an `ast.expr`. Uses `_resolve_type_name` /
`_resolve_qualified_type_name` only to disambiguate structural wrappers
(Ptr/Own/readonly/auto_readonly/auto_own from `tpy`; Optional/Final/
Callable/Literal from `typing`; tuple from `builtins`; Fn from `tpy`)
from user generics -- leaf names (primitives, user records, enums,
protocols, type parameters, qualified names) stay raw in
`TpyTypeRef.name`.

Resolution-time errors (unknown type, qualified-module-not-imported,
Own-in-union, readonly normalization) are deliberately not raised here
-- they belong to the resolver. Only syntax-provable errors (malformed
`Callable[[P1], R]` shape, `Literal` value kind mixing) are raised at
this layer.

No call sites use the new method yet. 35 unit tests in
`tpyc/test_parse_type_ref.py` pin the shape for representative
annotations (leaf names, generics, structural wrappers, unions,
callables, literals, dotted attributes, permissiveness on unknown
names).

##### F.3b.2 -- Parallel resolver + equivalence tests (DONE)

Added `Parser._resolve_type_ref_impl(ref, type_param_scope) -> TpyType`
in `tpyc/parse/parser.py`. Owns leaf name resolution + TpyType
construction + validation: primitive / registry lookup, type-param
substitution, generic arity via `lookup_generic_type`, structural
wrapper construction (with `Ptr[readonly[T]]` -> `PtrType(T,
is_readonly=True)` normalization), union own/readonly normalization,
nested dotted class lookup, qualified-module error hints, "Unknown
type" / unresolved-import errors.

Supporting changes:

- **`ParseError` extended** to accept an optional `loc: SourceLocation`
  fallback. Resolver-path errors populate `lineno` from `ref.loc` so
  error messages stay byte-identical to the ast-based path.
- **`_resolve_primitive_type`, `_resolve_registered_type`,
  `_raise_unresolved_import_error`** all made `node` optional and added
  `loc` kwarg. Existing callers (positional `node`) unchanged.
- **Walker fix** -- `_parse_subscript_type_ref` now builds the full
  dotted name for 3+ level Attribute chains (`a.b.c.D[T]`), so the
  resolver's nested-class fallback can match via
  `_resolve_dotted_class_name_str`. Previously raised-name was None for
  chains deeper than 2.
- **String-keyed variants** added alongside ast-keyed helpers:
  `_resolve_qualified_type_name_str(mod, attr)`,
  `_resolve_dotted_class_name_str(dotted)`,
  `_raise_unresolved_qualified_error_str(dotted, loc)`. Resolver uses
  these so it doesn't depend on ast structure.

**Not wired** -- `_parse_type_annotation` is unchanged; the resolver has
no call sites yet. That's F.3b.3's job.

**Tests** -- `TestResolverEquivalence` and `TestResolverErrors` classes
added to `tpyc/test_parse_type_ref.py`. For 68 annotation sources
covering primitives (fixed-int widths, floats, str family, bytes,
slice types, None), generics (list/dict/set/tuple/Array/Span with int
size), structural wrappers (Ptr/Own/readonly/auto_readonly/auto_own/
Optional/Final), unions, Callable/Fn, Literal, and deep nesting, plus
`T` / `list[T]` / `Ptr[T]` / `Array[T, N]` with type-parameter scopes,
the equivalence assertion is
`_parse_type_annotation(node) == _resolve_type_ref_impl(_parse_type_ref(node))`.
Error-lineno preservation pinned separately (`test_error_lineno_preserved`).

Full unit suite: 379 passed (311 + 68 new). Integration smoke green.
No call sites changed; dead code still.

##### F.3b.3 -- Flip `_parse_type_annotation` through the walker (DONE)

`_parse_type_annotation` rewritten to a 3-line router:

```python
def _parse_type_annotation(self, node, type_param_scope=None) -> TpyType:
    if type_param_scope is None:
        type_param_scope = self._type_param_scope
    ref = self._parse_type_ref(node, type_param_scope)
    return self._resolve_type_ref_impl(ref, type_param_scope)
```

External contract unchanged -- all ~30 call sites still receive
`TpyType`. `FragmentParser.` _parse_type_annotation` override
(`except ParseError` fallback for unresolved names in macro fragments)
still works because it calls `super()._parse_type_annotation`.

**Structural-wrapper name collision**: The initial implementation used
bare canonical names like `TpyTypeRef("Ptr", ...)` / `("Optional",
...)` for structural wrappers when the source name resolved. But the
generic-path fallback also emitted `TpyTypeRef(raw_name, ...)` where
`raw_name` was the user's source identifier -- so `Optional[T]`
written **without** `from typing import Optional` produced the same
shape as `Optional[T]` with the import, and the resolver silently
built `OptionalType` instead of raising the expected "requires: from
typing import Optional" error.

First fix-attempt used dotted qnames (`"tpy.Ptr"`, `"typing.Optional"`,
`"builtins.tuple"`). That resolved the bare-name collision but
introduced a second one: qualified dotted source like
`typing.Optional[T]` written without `import typing` also falls
through to the generic path with `raw_name = "typing.Optional"` -- same
string as the canonical qname. Resolver silently matched again.

Final form: **`:` separator for canonical names** (`"tpy:Ptr"`,
`"tpy:Own"`, `"tpy:readonly"`, `"tpy:auto_readonly"`, `"tpy:auto_own"`,
`"typing:Optional"`, `"typing:Final"`, `"builtins:tuple"`). `:` cannot
appear in any Python identifier or dotted-name form, so the canonical
strings are distinct from any raw user source the generic-path
fallback could produce. Regression pinned by
`cases/imports/error_typing_qualified_not_imported` (no diag change --
this case already raised under the ast path and raises again under the
ref path, after the fix).

**Verification:**
- Full unit suite: 379 passed.
- Full integration suite: 2714 passed + 1 skipped.
- `uv run pytest --force-exec` (rebuilds every case's exec phase,
  ignoring fingerprint-based skips): 2714 passed + 1 skipped. **Zero
  changes to `tests/` expected files** -- byte-identical generated C++
  across every case.
- The sema-side resolve pipeline (`TypeOperations.resolve_type`) sees
  the same `TpyType` it did before, so all downstream paths remain
  untouched.

##### F.3b.4 -- Infrastructure + first flipped site (DONE)

Scope reduction: land the end-to-end sema-side ref resolution pipeline
with a single flipped site (`TpyVarDecl.type`) as the proving case.
The remaining four leaf sites (TpyFunction params/return, record
fields, method signatures, overload stubs) move in F.3b.5+.

**Infrastructure:**

- `TpyModule.resolver: Callable | None`. Parser attaches
  `self._resolve_type_ref_impl` (bound method) at end-of-parse. Bound
  method retains the parser instance alive, so the resolution state
  (registry, imports, local_defs, module_class_names,
  module_type_alias_names, reverse_module_aliases, bare_module_imports)
  travels with it. Mirrors the parser-author / sema-reader pattern of
  `TpyModule.directives`.
- `SemanticContext.parser_resolver: Any`. `analyzer.analyze()` wires it
  from `module.resolver`.
- `TypeOperations.resolve_type_ref(ref, type_param_scope=None) ->
  TpyType`. Delegates to `ctx.parser_resolver`. When the caller omits
  `type_param_scope`, builds one from the current function/record
  context (`ctx.func.current_function.type_params`,
  `ctx.record_ctx.type_params` + kinds). The parser's own
  `_type_param_scope` is parse-time state and is unreliable at sema
  time, so the resolver must receive scope per-call for code inside
  generic bodies.
- `TypeOperations._current_type_param_scope()` helper builds the scope
  dict; zips record `type_params` + `type_param_kinds` (parallel lists
  on `TpyRecord`) and assigns `TypeParamKind.TYPE` uniformly for
  function params (`TpyFunction` doesn't carry kinds).

**Walker fix:** `_parse_type_ref` for `ast.Name` applies
`_nested_type_scope` eagerly. Referencing `Kind` inside
`class Message: class Kind: ...` emits `TpyTypeRef("Message.Kind",
...)` directly, so the emitted ref survives to sema time even though
`_nested_type_scope` is parse-time-only state. Without this, a deferred
resolution in sema would see a bare `TpyTypeRef("Kind", ...)` with no
way to find the dotted form.

**Flipped site: `TpyVarDecl.type`.** Parser's AnnAssign handler emits
`TpyTypeRef` via `_parse_type_ref` instead of `TpyType` via
`_parse_type_annotation`. `TpyVarDecl.type` field type widened to
`Optional[TpyType | TypeRefNode]`.

Three writer sites resolve + write back:
- `sema/analyzer.py::_resolve_pending_type_refs` -- new top-level
  pre-pass that runs before `_fix_recursive_optional_annotations`
  (which reads `stmt.type` and calls `typ.map_inner_types` --
  TypeRefNode doesn't have that). Resolves all `TpyVarDecl.type` refs
  in `module.top_level_stmts` to `TpyType` in place.
- `sema/registration.py::register_globals` -- defensive resolution at
  top of the loop. In practice, already-resolved by the pre-pass, but
  kept so future changes don't regress.
- `sema/statements.py::_analyze_var_decl` -- defensive resolution at
  top. Needed for TpyVarDecl inside function bodies (which aren't in
  top_level_stmts and aren't touched by the pre-pass).

**ParseError extension re-used.** The `loc` kwarg added in F.3b.2
carries through: errors raised by the sema-side resolver path still
report correct line numbers via `ref.loc`.

**Verification:**
- Unit suite: 379 passed.
- Integration suite: 2714 passed + 1 skipped.
- `uv run pytest --force-exec`: 2714 passed + 1 skipped, zero changes
  to any `tests/expected/` files. Byte-identical generated C++.

##### F.3b.5 -- Flip top-level function signatures + record fields (DONE)

Scope reduced from the original "flip remaining four sites" to the two
that don't require rearchitecting parse-time type inspection. Record
methods and protocol method signatures stay on the TpyType path; they
move in a later sub-step together with the parse-time logic that
depends on them.

**Flipped:**
- `TpyFunction.params` / `.return_type` / `.vararg_type` for top-level
  functions (`_parse_function`, including `@overload` stubs and
  `@builtin_decorator` stubs).
- `TpyRecord.fields[i].type` (FieldInfo emission in `_parse_class`).

**Kept on TpyType path (parse-time resolution still required):**
- Record methods (`_parse_method`) -- inspect param types at parse
  time for `@auto_readonly` wrapping and property-setter `Own[T]`
  promotion.
- Protocol method signatures (lines 1741+, `MethodSignature`).
- Nested-def bodies and macro-fragment functions -- resolved eagerly
  via the new `Parser._finalize_function_refs(func)` helper so the
  enclosing scope's type params don't get lost.

**AST widening** (`tpyc/parse/nodes.py`):
- `TpyFunction.params: list[tuple[str, TpyType | TypeRefNode]]`
- `TpyFunction.return_type: TpyType | TypeRefNode`
- `TpyFunction.vararg_type: TpyType | TypeRefNode | None`
- `TpyFunction.type_param_kinds: list[TypeParamKind]` new parallel
  field. `_parse_function` populated it locally before but never
  stored; F.3b.5's sema-side `_resolve_pending_type_refs` needs
  per-function INT/TYPE kinds to build resolution scope correctly
  (otherwise `def f[T, N: int](a: Array[T, N])` would resolve N as
  TYPE and reject the int-kind slot).
- `FieldInfo.type` annotation stays `TpyType` (typesys module, no
  parse-time imports) but is dynamically a TypeRefNode between parse
  and the pre-pass; runtime isinstance at the pre-pass covers the
  cross-type handling.

**Parser changes** (`tpyc/parse/parser.py`):
- `_parse_function` param / vararg / kwonly / return sites flipped to
  `_parse_type_ref`.
- `_parse_class` field-annotation site flipped to `_parse_type_ref`.
- `_parse_nested_def` and `FragmentParser.parse_fragment` call the
  new `_finalize_function_refs(func)` after `_parse_function` so
  nested / macro-fragment functions immediately have TpyType
  signatures.
- `@builtin_decorator` stub path in `_parse_module` also calls
  `_finalize_function_refs` before `_schema_from_stub` (which inspects
  param types at parse time to derive decorator argument schemas).

**Sema changes** (`tpyc/sema/analyzer.py`, `tpyc/sema/type_ops.py`):
- `_resolve_pending_type_refs` moved to run before
  `register_record` / `register_function`, so all downstream passes
  read TpyType uniformly.
- `_resolve_pending_type_refs` extended to walk `module.functions`
  (params / return_type / vararg_type) and `module.all_records()`
  fields.
- Per-function scope built from `type_param_kinds` list so INT-kind
  type params survive the ref->type transition.
- `TypeOperations._current_type_param_scope()` also reads
  `type_param_kinds` for the current function (was hard-coded TYPE).

**Test updates** (`tpyc/test_parse.py`):
- `TestParserStateIsolation`'s three tests previously asserted
  `pytest.raises(ParseError)` on the second parse of names that used
  to fail at parse time. Those names now fail in sema, so the tests
  are refactored to verify state isolation directly: check that the
  leaked import is absent from `module.imports`, then call the
  attached `module.resolver` on the unresolved ref and assert it
  raises.

**Verification:**
- Unit suite: 379 passed.
- Integration suite: 2714 passed + 1 skipped.
- `uv run pytest --force-exec`: 2714 passed + 1 skipped, zero changes
  to any `tests/expected/` files. Byte-identical generated C++.

##### F.3b.6 -- Parser purity for methods (DONE)

F.3b.1--F.3b.5 reached the limit of what can be flipped without moving
parse-time semantic logic. `_parse_method` still inspects resolved
types for decorator-driven transformations (`@auto_readonly` wrapping,
property-setter `Own[T]` promotion) and produces method clones
(`_clone_auto_readonly`, `_clone_auto_own`) via `strip_auto_readonly`
/ `apply_auto_readonly` tree walks on TpyType. The self annotation is
also inspected at parse time to derive `is_consuming` / `auto_own` /
`auto_readonly` flags. To flip `TpyRecord.methods` + protocol method
signatures to `TpyTypeRef`, that logic has to move to sema.

**Target architecture** (parser-purity design):

Parser emits one TpyFunction per source def with only syntactic
intent:

- Decorators as flags (unchanged: `auto_readonly`, `auto_own`,
  `is_readonly`, `is_property_setter`, `is_staticmethod`, ...).
- Self annotation as `TpyTypeRef` (no inspection).
- Params / return / vararg as `TpyTypeRef`.
- No cloning at parse time.

Sema gains `sema/method_expansion.py` running between
`_resolve_pending_type_refs` and `register_enum` / `register_record`:

1. `_derive_self_flags` -- walks resolved self type. `Own[Self]` ->
   `is_consuming`. `AutoOwn[Self]` -> `auto_own`.
   `AutoReadonly[Self]` -> `auto_readonly`. Validates incompatible
   combinations (`__init__` / `__del__` x ownership, `@readonly` x
   `Own[Self]`, etc.). Errors raised as `SemanticError`, not
   `ParseError`.
2. `_apply_auto_readonly_wrapping` -- if `method.auto_readonly`,
   scan resolved params and wrap non-value non-readonly with
   `AutoReadonlyType`. Also detect per-param `AutoReadonlyType`
   that propagates the flag.
3. `_apply_property_setter_wrapping` -- if `is_property_setter`,
   wrap first param with `Own[T]` when not value-type.
4. `_expand_clones` -- methods with `auto_readonly` -> mutable/const
   clones via `strip_auto_readonly` / `apply_auto_readonly`.
   Methods with self `auto_own` -> borrowing/consuming clones.
   Clones inserted into `record.methods` before registration.

**Known user-facing side effects:**

- Error type shift: validation errors like "Own[Self] is not allowed
  on __init__" move from `ParseError` to `SemanticError`. User-facing
  `file.py:N: error: msg` format is preserved (same lineno via
  `ref.loc`), but tests that do `pytest.raises(ParseError)` on these
  specific messages need to switch to `SemanticError`. Audit needed;
  expect ~5-10 test cases.
- `FragmentParser.parse_fragment` output for `quote_fun` / macros
  currently returns a TpyFunction with wrapping + clones applied.
  After F.3b.6, fragment output is the raw (unexpanded) form; sema
  re-runs method_expansion on macro-generated methods. Macro consumers
  in `tplib` need audit.

**Sub-steps:**

##### F.3b.6.1 -- Introduce method_expansion.py; move self-annotation validation (DONE)

Implementation split from the plan's literal wording for byte-identical
codegen: the self-annotation *validation* checks move to sema, but the
parser still sets `is_consuming` / `auto_own` / `auto_readonly` flags
because parse-time cloning (`_clone_auto_readonly` / `_clone_auto_own`)
still consumes them. F.3b.6.2 moves cloning + flag derivation together;
F.3b.6.3 flips the method signature shape.

- `sema/method_expansion.py` added with `expand_methods(module)` entry
  point. Currently walks `module.all_records()` methods and validates
  self-annotation constraints.
- Parser's `_parse_method` no longer raises `ParseError` for:
  - `Own[Self]` / `auto_own[Self]` / `auto_readonly[Self]` on
    `__init__` / `__del__`
  - `Own[Self]` / `auto_own[Self]` combined with `@readonly`
- Parser preserves the resolved self annotation on
  `TpyFunction.self_annotation` (new field, carried through
  `dataclasses.replace` on clones). The borrowing / mutable clone half
  is skipped in sema to avoid duplicate diagnostics.
- Two checks stay in `_parse_method` until F.3b.6.2 (need pre-clone
  `is_readonly` / `auto_readonly_dec` state):
  - `self: auto_readonly[Self]` + `@readonly`
  - `self: auto_readonly[Self]` + `@auto_readonly` decorator
- `expand_methods` runs in `analyzer.analyze()` between
  `_resolve_pending_type_refs` and `register_enum`.
- No test snapshot changes: `ParseError` and `SemanticError` produce
  identical `file:line: error: message` output, and messages are
  preserved verbatim.
- Byte-identical codegen: `uv run pytest --force-exec` is 2718 passed
  + 1 skipped with zero changes to `tests/cases/**/expected/`.

##### F.3b.6.2 -- Move auto_readonly + property-setter wrapping + cloning (DONE)

- `sema/method_expansion.expand_methods` now runs four transformation
  steps per method: self-flag derivation (absorbs F.3b.6.1's parser
  behaviour), self-annotation validation, param wrapping
  (`@auto_readonly` decorator + per-param `auto_readonly[T]` detection
  + property-setter `Own[T]`), and cloning (auto_readonly ->
  mutable/const; auto_own -> borrowing/consuming).
- Parser's `_parse_method` shed:
  - self-annotation flag setting for `is_consuming` / `auto_own` /
    `auto_readonly` (sema derives from `self_annotation`)
  - `@auto_readonly` decorator param wrapping loop
  - per-param `auto_readonly[T]` detection
  - late (non-decorator) `auto_readonly` + method-type-params guard
  - property-setter first-param `Own[T]` wrapping
  - two remaining self-annotation validation checks (combined with
    `@readonly`, combined with `@auto_readonly` decorator)
- Parser's `_parse_class` no longer calls `_clone_auto_readonly` /
  `_clone_auto_own`; methods append as-is. The helpers are deleted.
- Parser retains the decorator-scoped `@auto_readonly` guards (no
  combining with `@readonly` / `@staticmethod` / `__init__` / `__del__`,
  no combining with method-level type params) because firing them at
  parse time keeps the diagnostic pinned to the decorator location and
  short-circuits before other parse-time checks (e.g. stub body
  validation) mask them.
- New `TpyFunction.has_auto_readonly_decorator` flag carries "had the
  `@auto_readonly` decorator" through to sema for the wrapping step.
- `register_record` now sees the expanded method list because
  `expand_methods` replaces `record.methods` in place with the
  result of expansion, matching pre-F.3b.6 shape.
- Byte-identical codegen: `uv run pytest` + `--force-exec` both pass
  2718 + 1 skipped with zero `tests/cases/**/expected/` diffs.

##### F.3b.6.3 -- Flip `_parse_method` + protocol method signatures to TpyTypeRef (DONE)

- `_parse_method` now emits `TypeRefNode` via `_parse_type_ref` for
  method params, return_type, vararg_type, kwonly params, and the
  self annotation. Protocol `MethodSignature` params / return_type
  same.
- `_resolve_pending_type_refs` extends to walk record methods and
  protocol method signatures:
  - Record methods resolve with a **merged scope** (record type
    params + method type params; method wins on collision).
  - Self annotation is resolved here so sema's `expand_methods` sees
    TpyType when deriving flags / validating shape.
  - Protocol methods resolve under the protocol's own type-param
    scope.
- `MethodSignature.params` / `return_type` in `typesys.py` widened to
  `TpyType | TypeRefNode` via a TYPE_CHECKING-only import (no
  circular-import fallout). `TpyFunction.self_annotation` similarly
  widened in `parse/nodes.py`.
- Parser's catch-all "Only 'Own[Self]' / 'auto_own[Self]' /
  'auto_readonly[Self]' is allowed" shape check moves to
  `sema.method_expansion._validate_self_annotation` (it needs a
  resolved TpyType; was the last parser-side reason to call
  `_parse_type_annotation` in the method path).
- Parser no longer calls `_parse_type_annotation` anywhere in
  `_parse_method` or the protocol method path.
- `FragmentParser` still calls `_finalize_function_refs` -- macro
  consumers need TpyType immediately and don't flow through the
  module-level pre-pass. Auditable in F.3b.6.4 if needed; no
  macro-visible regression today.
- Byte-identical codegen: `uv run pytest` + `--force-exec` both pass
  2718 + 1 skipped with zero expected/ diffs.

##### F.3b.6.4 -- Cleanup (DONE)

Audit of the method path after F.3b.6.3 shows cleanup is mostly a no-op:

- `_parse_type_annotation` no longer appears in the method signature
  path (params / return / vararg / kwonly / self) or the protocol
  method signature path. The one remaining call in `_parse_method` is
  on type-parameter **bounds** (`def foo[T: Bound]`), which is a
  narrow well-scoped resolution that intentionally stays parser-side
  for now -- bounds don't flow through the sema resolve pre-pass and
  the check expects `NominalType` immediately.
- `_finalize_function_refs` calls survive only in paths that legitimately
  need TpyType synchronously (nested defs inside function bodies,
  @builtin_decorator stubs, macro fragments via FragmentParser).
  None of these are method-body calls.
- `_parse_method` still contains:
  - The `@auto_readonly` decorator-scoped guards (combinability with
    `@readonly` / `@staticmethod` / `__init__` / `__del__`, and the
    early method-level-type-params guard). These stay because they
    point the diagnostic at the decorator's own line via
    `auto_readonly_dec`, which sema can't easily replicate without
    extra location plumbing.
  - Decorator parsing, defaults, generator detection, `__exit__`
    param shape check, `__next__` error_return defaulting. These are
    pure parser concerns that don't look at resolved types.

Phase F.3b.6 lifts parser / sema boundary for methods to the target
shape: parser emits TpyTypeRef for every signature site, sema's
`_resolve_pending_type_refs` + `expand_methods` own flag derivation,
validation, wrapping, and cloning.

**Overall F.3b.6 verification:**
- Unit suite: 383 passed (+4 vs. F.3b.5 baseline).
- Integration suite: 2718 passed + 1 skipped (+4 vs. F.3b.5 baseline).
- `uv run pytest --force-exec`: 2718 passed + 1 skipped, zero diffs
  under `tests/cases/**/expected/`.

##### F.3b.6.5 -- Route macro-added methods through `expand_methods` (DONE)

F.3b.6 left one latent gap: `_apply_class_macros` ran inside
`register_record` *after* the module-level `expand_methods` pass, so
methods added by a macro (via `ClassInfo.add_method`) skipped every
expansion step. If a macro ever produced a method with
`@auto_readonly`, per-param `auto_readonly[T]`, or
`self: Own[Self] | auto_own[Self] | auto_readonly[Self]`, registration
would trip "auto_readonly[T] in return type is only allowed on
@auto_readonly methods" or simply emit uncloned output.

- Moved the expansion call site into `register_record`, running right
  after `_apply_class_macros`. The module-level
  `expand_methods(module)` entry point still exists for convenience
  but nothing in the analyzer calls it. Order inside `register_record`
  is now: stub RecordInfo, field validation (resolves field types),
  `__del__` validation, `_apply_class_macros`,
  `expand_methods_for_record`, duplicate-method check (runs against
  the fully expanded list so clone flags exempt correctly), field
  default const-validation, rest of registration.
- Idempotency: on first expansion, clones get `self_annotation=None`
  so a second pass through `_expand_one` sees no derivation source,
  flags stay False, and no re-cloning happens. Pinned by
  `tpyc/test_method_expansion.py`.
- No macro today actually generates expansion-triggering methods, so
  full suite + `--force-exec` remain byte-identical. Unit suite grew
  by 6 (method_expansion tests).

##### F.3b.6.6 -- FragmentParser method mode (DONE)

Closed the last F.3b.6 gap: macros that go through
`ast.quote_fun` / `cls.add_method_from_source` can now emit methods
with full method semantics.

- `FragmentParser.parse_fragment(kind="function")` detects a method by
  the leading `self` param and routes through `_parse_method` (with
  synthetic `class_name="<macro>"`, no property-name context) instead
  of `_parse_function`. `self_annotation` and
  `has_auto_readonly_decorator` get populated; `self` is stripped
  from `params` inside `_parse_method`, matching source-defined
  methods.
- `_finalize_function_refs` only resolves params / return / vararg
  TpyTypeRefs. The new code path also resolves `self_annotation`
  explicitly under the method's own type-param scope so
  `expand_methods_for_record` sees TpyType.
- Regression test: `tests/cases/records/macro_quote_auto_readonly` --
  a macro emits `@auto_readonly def first(self) -> Int32`; the
  generated C++ shows both `first()` and `first() const` clones, and
  the readonly-receiver call path type-checks.
- `@property` / `@property.setter` decorators still aren't usable from
  FragmentParser because `property_names` is None (the setter's
  `@x.setter` falls through to the "unknown decorator" handler). This
  matches pre-F.3b.6.6 behaviour and is a separate concern; fixing it
  requires giving the macro API a way to emit getter + setter as a
  pair so FragmentParser can thread `property_names` between them.

#### Phase F.3c -- Expand `TpyTypeRef` coverage to all annotation sites

Audit of the original list against the actual parser call sites
(post-F.3b.6): items 5-7 (`TpyExceptHandler.type`, `TpyWithItem.enter_type`,
match-pattern class names) are already either strings that sema resolves
or sema-inferred fields, not parser-parsed types. Item 1
(`TpyCall.isinstance_type`) is also sema-inferred. The real remaining
sites are covered by four sub-steps:

##### F.3c.1 -- Type parameter bounds (DONE)

Flipped the three `tp.bound` call sites (record-level at
`_parse_class`, method-level in `_parse_method`, free-function in
`_parse_function`) from `_parse_type_annotation` to `_parse_type_ref`.
Widened `TpyRecord.type_param_bounds` and `TpyFunction.type_param_bounds`
value types to `TpyType | TypeRefNode`. `_resolve_pending_type_refs`
extended with a `_resolve_bounds` helper that walks record / method /
free-function bounds dicts under a None scope (bounds reference
protocols in scope, not other type params -- matches pre-flip
behaviour).

Removed the parser's "bound must be NominalType" check; sema's
`_resolve_type_param_bounds` already asserts `is_protocol_type(resolved)`
(which is strictly stronger -- it checks `NominalType AND is_protocol`).
Diagnostic text shifts from `"Type parameter bound must be a protocol
or 'int', got X"` at parse time to `"Type parameter bound must be a
protocol, got X"` at sema. No diag.txt tests depend on the exact text.

Byte-identical codegen: `uv run pytest` + `--force-exec` both 2730
passed + 1 skipped with zero `tests/cases/**/expected/` diffs.

##### F.3c.2 -- Declaration-body type references (PARTIAL)

Flipped two of the four originally-planned sites:

- **Protocol field types** (`TpyProtocol.fields`): parser emits
  `TypeRefNode`; sema resolves under the protocol's type-param scope.
- **`**kwargs: Unpack[TypedDict]`** (`TpyFunction.kwarg_type`): parser
  emits `TypeRefNode` for the inner TypedDict; sema resolves in the
  pre-pass.

`TpyProtocol.fields` value type widened to `TpyType | TypeRefNode`;
`TpyFunction.kwarg_type` widened similarly. Byte-identical codegen:
2730 + 1 skipped under `--force-exec`.

##### F.3c.2b -- Class bases (DONE, pragmatic)

Flipped `TpyRecord.bases` to `list[TpyType | TypeRefNode]`. Parser
emits `TypeRefNode` via `_parse_type_ref`, `_resolve_pending_type_refs`
walks bases under the record's type-param scope.

The parser retains a parse-time `_resolve_type_ref_impl` call on each
base, discarding the resolved TpyType and storing the `TypeRefNode`.
This is a deliberate side-effect: it keeps the "Unknown type",
"Unsupported qualified type", and "<name> requires: from <mod> import
<name>" hints firing at the class-header line rather than being masked
by later parse-time class-body checks (stub-body validation,
field-type inference on `Red = auto()` when `Enum` is shadowed, etc.).

Architecturally a loose end: ideally class-body validation would move
to sema so sema-time base resolution errors fire naturally first,
removing the need for the parse-time side-effect. That's a larger
refactor across stub-body validation, field auto-declare, enum
dispatch, TypedDict handling, etc., and out of scope for F.3c.

Byte-identical codegen: 2730 passed + 1 skipped under `--force-exec`.

##### F.3c.2c -- Type alias bodies (DONE)

Flipped `TpyModule.type_aliases` value types from
`tuple[TpyType, SourceLocation | None]` to
`tuple[TpyType | TypeRefNode, SourceLocation | None]`. Parser's
`_register_type_alias` now uses `_parse_type_ref` (no parse-time
resolution, no parse-time recursive-union detection).

Sema's `_resolve_pending_type_refs` gains an early alias-resolution
pass that runs before records / methods / functions. For each alias in
declaration order: it sets `parser._pending_alias_name` so the
resolver produces `NominalType(name)` placeholders for same-body
self-references, resolves the RHS ref, then registers the resolved
type in parser.registry so subsequent aliases can look it up by name.
Recursive unions are detected post-resolution (same logic moved from
parser).

Forward references between aliases (`type B = list[A]` before
`type A = int`) continue to work via the existing
`_module_type_alias_names` pre-scan + later substitution machinery
that was already load-bearing pre-flip.

Byte-identical codegen: 2730 passed + 1 skipped under `--force-exec`.

##### F.3c.3 -- Expression-level type uses (DONE)

Flipped `TpyCall.call_type`, `TpyCall.type_args`, and
`TpyMethodCall.type_args` to `TypeRefNode` at the emission sites
(`_parse_call` `call_type` branch, `_parse_type_args_from_subscript`).
Parser now catches only structural errors at parse time (e.g.
integer literals at type-arg positions); name-resolution errors
defer to sema.

Sema's `_resolve_pending_type_refs` gains a recursive body-walker pass
that finds every `TpyCall` / `TpyMethodCall` in function /
method / top-level / nested-def bodies and resolves `call_type` +
`type_args` under the enclosing function's type-param scope. Matches
pre-flip silent-failure semantics: `call_type` resolution errors
clear the field (sema falls back to `type_args` / `subscript_callee`);
`type_args` resolution errors clear the tuple and populate
`type_args_parse_error`, which sema's generic-call validator already
reports at line 2106 in `calls.py`.

Internal resolver helpers (`_parse_record_type_args`,
`_parse_protocol_type_args`, `_parse_tuple_type`, `_parse_generic_type`)
are dead code after F.3b.3 routed everything through `_parse_type_ref`;
their deletion is scheduled for F.3c.4.

Byte-identical codegen: 2730 passed + 1 skipped under `--force-exec`.

##### F.3c.4 -- Cleanup (DONE)

Deleted four dead parser helpers that were already unreachable after
F.3b.3 routed annotation parsing through `_parse_type_ref`:
`_parse_protocol_type_args`, `_parse_record_type_args`,
`_parse_tuple_type`, `_parse_generic_type`. Their ref-based variants
(`_resolve_protocol_type_args`, `_resolve_record_type_args`, the
inline tuple / generic paths in `_resolve_type_ref_impl`) are the
only alive code paths now.

`_parse_type_annotation` survives as a thin wrapper around
`_parse_type_ref + _resolve_type_ref_impl`. Its remaining callers:

- `FragmentParser._parse_type_annotation` (override with lenient
  NominalType fallback for macro fragments with unknown type names).
- Equivalence tests in `tpyc/test_parse_type_ref.py` that pin the
  walker+resolver composition against the annotation path.

Both are legitimate uses of the public helper. No further cleanup in
F.3c.

After F.3c, the AST contains `TpyType` only for sema-resolved results
(parser-emission path is uniformly `TypeRefNode`). The two F.3c debts
(parse-time resolve-for-side-effect on class bases, sema mutating
`parser._pending_alias_name`) are tracked in TODO.md "Typesys
migration followups" and become cheap to delete once F.3d moves
primitive resolution out of the parser.

Byte-identical codegen: 2730 passed + 1 skipped under `--force-exec`.

**Architectural debt tracking:** F.3c.2b and F.3c.2c each introduced a
load-bearing workaround (parse-time resolve-for-side-effect on bases;
sema mutating `parser._pending_alias_name` via `resolver.__self__`).
Both are tracked under "Typesys migration followups" in
`TODO.md` with the path to removal. They become cheap to delete after
F.3d moves primitive resolution out of the parser.

#### Phase F.3d -- Move name resolution into a sema-owned resolver (DONE)

Four sub-steps on `typesys-migration-phase-F3d`, each byte-identical
under `uv run pytest --force-exec` (2730 passed + 1 skipped, zero diffs
in `tests/cases/**/expected/`). Both F.3c debts -- parse-time
resolve-for-side-effect on class bases (F.3c.2b) and sema mutating
`parser._pending_alias_name` via `resolver.__self__` (F.3c.2c) --
fully deleted.

##### F.3d.1 -- Extract `TypeResolver` into `tpyc/type_resolver.py` (DONE)

Moved all name-to-TpyType resolution machinery out of the parser into a
new `TypeResolver` class that holds a back-reference to the parser and
reads live state (registry, imports, local_defs, nested_type_scope,
module_class_names, module_type_alias_names, bare_module_imports,
reverse_module_aliases) on each call, so container growth during parse
-- and re-assignment of per-parse containers at the top of each
`parse()` -- is visible. Parser constructs a single `TypeResolver`
instance in `__init__` and attaches it to `TpyModule.resolver` at the
end of each `parse()` call; sema's `TypeOperations.resolve_type_ref`
calls `resolver.resolve(...)` explicitly instead of treating the
resolver as a bound-method callable.

Relocated from parser: `_resolve_type_ref_impl` (became
`TypeResolver.resolve`), `_resolve_primitive_type`,
`_resolve_registered_type`, `_resolve_qualified_type_name_str`,
`_resolve_dotted_class_name_str`, `_raise_unresolved_qualified_error_str`,
`_resolve_generic_type_from_ref`, `_resolve_record_type_args_from_ref`.
`_FIXED_INT_MAP` also relocated; parser re-imports it for the callers
that stayed behind (`_get_default_value`, `_validate_const_default`,
`_infer_type_from_expr`). Kept on parser (FragmentParser overrides or
walker-time use): `_resolve_type_name`, `_resolve_qualified_type_name`,
`_resolve_dotted_class_name`, `_resolve_parser_keyword`,
`_raise_unresolved_import_error`.

Parser keeps a one-line `_resolve_type_ref_impl` delegate for the test
equivalence suite and the macro fragment `self_annotation` path.
`sema/analyzer.py` temporarily uses `resolver._parser` instead of
`resolver.__self__` (removed in F.3d.3).

##### F.3d.2 -- Prune parser typesys imports (DONE)

With the resolver extracted, parser.py's `typesys` imports reduce to
runtime-needed symbols only:

- **Info / bookkeeping:** `FieldInfo`, `RecordInfo`, `FunctionInfo`,
  `MethodSignature`, `ProtocolInfo`, `TypeRegistry`
- **Walker state / output:** `TypeParamKind`, `LiteralValue`
- **Small primitive set** for field-inference / return-type defaults:
  `VOID`, `STR`, `FLOAT`, `BIGINT`, `NominalType`
- **Helpers:** `ensure_qualified`, `public_module_name`

`TpyType` moved behind `TYPE_CHECKING` (used only as string-form
annotation under `from __future__ import annotations`). Dropped all
structural-type imports (`PtrType`, `OwnType`, `ReadonlyType`,
`AutoReadonlyType`, `AutoOwnType`, `FinalType`, `SelfType`,
`TypeParamRef`, `OptionalType`, `VoidType`, `make_union`, `UnionType`,
`TupleType`, `CallableType`, `make_fn_type`, `INT32`, `STRING`,
`STRVIEW`, `CHAR`, `BYTES`, `BYTEARRAY`, `BYTESVIEW`, `BOOL`,
`FLOAT32`, `SELF`, `BASIC_SLICE`, `SLICE`, `LiteralType`,
`ALL_FIXED_INTS`) and dead imports left over from F.3c.2c
(`_contains_self_reference`, `validate_recursive_union_paths`). Also
dropped `BuiltinTypeDef` / `get_type_factory_param_kinds` (used only
by the moved resolver methods) and the bulk `lookup_generic_type`
re-export (one remaining site uses a local import). Kept
`lookup_generic_type_in_module` for the subscript-as-value check in
`_parse_expr`.

Deleted the now-dead `_raise_unresolved_qualified_error` (node-based,
unreachable since F.3c.2b routed everything through the loc-based
variant that moved to TypeResolver in F.3d.1).

The `qnames` module import stays in parser.py -- still needed for
decorator-qname matching (out of scope for F.3d; candidate for a later
cleanup that refactors decorator handling).

##### F.3d.3 -- Thread `pending_alias` through the resolver API (DONE)

`TypeResolver.resolve(...)` gains a keyword parameter
`pending_alias: str | None = None`. When set (by sema's alias pass), it
is stored on `self._pending_alias` for the duration of the call;
recursive re-entries through the public `resolve()` without
`pending_alias` inherit the context naturally, and it is restored on
exit. `_resolve_registered_type` consults `self._pending_alias` instead
of `parser._pending_alias_name`.

`TypeOperations.resolve_type_ref` forwards `pending_alias` to
`resolver.resolve`. Sema's `_resolve_pending_type_refs` alias pass
passes `pending_alias=alias_name` as a keyword arg instead of mutating
`parser._pending_alias_name` via `resolver.__self__`. Added a
`TypeResolver.registry` property so sema's alias pass does
`resolver.registry.register_type_alias(...)` instead of reaching
through `resolver._parser.registry.register_type_alias(...)`.

Parser loses its `_pending_alias_name` field. F.3c.2c debt deleted,
and the broader `resolver.__self__` read-through pattern called out
in TODO.md is no longer used anywhere.

##### F.3d.4 -- Move class-body validation to sema (DONE)

Deletes the parse-time resolve-for-side-effect call at `_parse_class`
that existed purely to fire base-resolution errors at the class-header
line before parse-time class-body checks (stub-body validation, field
auto-declare for `Red = auto()` when `Enum` is shadowed) masked them.

Three parse-time class-body validation sites move to sema, where they
naturally run after `_resolve_pending_type_refs` has surfaced any base
resolution errors:

- Stub-body / `@native` decorator restrictions (`is_stub` on DEFAULT
  linkage, `native_name` on DEFAULT linkage, missing stub body on
  non-DEFAULT linkage) move into a new analyzer method
  `_validate_record_method_linkage` called right after
  `_resolve_pending_type_refs`. Errors attach to `record.loc` so the
  diagnostic points at the class header, matching the pre-F.3d.4
  parser-raised variant that passed the class's ast node to
  `ParseError`.
- "Cannot infer type for field 'X'" failure for unannotated class-body
  assignments like `Red = auto()` is deferred via a new
  `TpyInferFromDefaultRef` marker node. Parser emits the field with
  this marker when `_infer_type_from_expr` returns None (while still
  parsing the default as TpyExpr for downstream passes). Sema's
  field-resolution loop raises the `SemanticError` only when it
  encounters the marker -- by which point base errors on the
  enclosing record have already fired.

Pinning tests restored byte-identically: `error_shadow_protocol` now
sees `'Protocol' requires: from typing import Protocol` from sema
base resolution; `error_enum_base_shadowed` sees `Unknown type: Enum`;
`error_enum_module_shadowed` sees `Unsupported qualified type:
enum.Enum`. F.3c.2b debt deleted.

#### Phase F.3e -- Delete the type factory table (DONE)

Four sub-steps on `typesys-migration-phase-F3e`, each byte-identical
under `uv run pytest --force-exec` (2768 passed + 1 skipped, zero diffs
in `tests/cases/**/expected/`). The old `modules/type_resolution.py`
factory table (`get_type_factory_param_kinds`, `lookup_generic_type`,
`lookup_generic_type_in_module`, `get_type_factory`,
`get_type_factory_names`, `_get_type_factories`,
`_make_factory_type_def`) is gone; the `BuiltinTypeDef` and
`GenericTypeLookup` dataclasses it powered are gone too.

The "Remaining parser typesys coupling" item that was in the F.3
problems list was not bundled into F.3e; it landed as **Phase F.3f**
instead. See that sub-section below for the six-sub-step breakdown.

##### F.3e.1 -- Add factory payload to TypeDef (DONE)

`TypeDef` gains two fields: `param_kinds: tuple[TypeParamKind, ...]`
(tuple so static registry entries are effectively immutable) and
`type_factory: Optional[Callable[..., TpyType]]`. A new
`_populate_factories()` pass (called from `_populate()`) attaches
factory payloads to 32 of the 34 existing TypeDefs (CopyIter and
OwnIter get no factory -- they are compiler-internal adapters
constructed only by `copy_iter()` / `own_iter()` builtins) and
registers a single new `tpy.Ptr` entry.

`tpy.Ptr` is the only structural wrapper that the old factory table
needed to carry -- `PtrType` instances are not `NominalType`, so the
TypeDef registry previously had no entry for them. A new
`TypeCategory.STRUCTURAL_WRAPPER` lets the one Ptr entry share the
registry without claiming to be a nominal category. No `_is_cat`
predicate tests this category, and `PtrType` dispatch stays on its
own class -- the TypeDef exists solely so the parser/sema resolver
can look up Ptr's `param_kinds` and `type_factory` through the
unified API.

Typesys imports happen lazily inside `_populate_factories()` to keep
module-load order safe: `typesys.py` imports `type_def_registry`
near the end (after all singletons and `make_*` helpers are
defined), and `type_def_registry`'s population runs before those
imports, so deferring the typesys import until the populate call
resolves the circular dependency cleanly.

Three small helpers land alongside the fields:
`find_factory_by_simple_name(name)` (scans `builtins` then `tpy`,
mirroring the old `lookup_generic_type`),
`find_factory_in_module(name, module)` (mirror of
`lookup_generic_type_in_module`), and `factory_qnames_in_module(mod)`
(mirror of `get_type_factory_names`, used by `register_tpy_star_import`).

`FACTORY_SNAPSHOT` conformance fixture pins
`qname -> tuple[TypeParamKind, ...]` for every factory entry and
gates both directions ("every registered factory has a snapshot
entry" and vice versa). Supporting tests verify the canonical-arg
construction actually returns a value whose `qualified_name()`
matches the TypeDef qname, that `STRUCTURAL_WRAPPER` holds only
`tpy.Ptr`, and that the three new helpers agree with the old
factory-table lookup semantics.

##### F.3e.2 -- Route callers through TypeDef (DONE)

Every public-API caller of the factory table switches to TypeDef:

- `tpyc/typesys.py::is_known_type`: membership check now reads
  `get_type_def(...).type_factory is not None`.
- `tpyc/compiler.py::_index_builtin_type_records`: reads
  `td.type_factory` instead of `get_type_factory(qname)`.
- `tpyc/sema/registration.py::register_tpy_star_import`: iterates
  `factory_qnames_in_module("tpy")`.
- `tpyc/sema/type_ops.py::validate_type`: arity lookup via
  `get_type_def` + `td.param_kinds`.
- `tpyc/sema/expressions.py`, `tpyc/sema/statements.py`,
  `tpyc/sema/calls.py`: eight call sites total that used to read
  `lookup.qualified_name` + `lookup.type_def.type_params` now read
  `td.qname` + `bool(td.param_kinds)`. Two sites that fabricated
  `GenericTypeLookup(None, qname)` to carry a fallback qname (for
  types imported from submodules without a simple-name factory)
  now track a plain `lookup_qname: str | None` -- same control
  flow, no wrapper.
- `tpyc/type_resolver.py`: imports shift from `tpyc.modules` to
  `tpyc.type_def_registry`; `_resolve_generic_type_from_ref` is
  typed against `TypeDef` instead of `BuiltinTypeDef`.
- `tpyc/parse/parser.py`: the two subscript-as-value existence
  checks (to raise "Generic type 'X' cannot be used as a value")
  now call `find_factory_by_simple_name` /
  `find_factory_in_module`.

Factory-table code stays in place at this step so each caller
migration can be verified independently.

##### F.3e.3 -- Delete the factory module (DONE)

Factory-table code removed from `modules/type_resolution.py`. The
`BuiltinTypeDef` and `GenericTypeLookup` dataclasses in
`modules/defs.py` deleted (they existed purely for the old return
types). `modules/__init__.py` drops the factory re-exports entirely
(including the orphaned `TypeParamKind` re-export that briefly
lived there during migration).

Two stragglers surfaced only at this deletion step because they
reached into the private `_get_type_factories()` rather than the
public surface swept in F.3e.2:
- `tpyc/modules/registry.py::get_builtin_type_obj` (used by sema
  to map a qname to a singleton during module registration).
- `tpyc/compiler.py::_resolve_builtin_self_refs` (substitutes
  `NominalType` for singleton factories in `@builtin_type` method
  signatures).
Both now consult `get_type_def` + `td.type_factory` /
`td.param_kinds`. The `--force-exec` suite catches any behavioral
drift if these two diverge from the old factory-table semantics.

##### F.3e.4 -- Collapse `validate_type` fallback (DONE)

`sema/type_ops.py::validate_type`'s two-pass fallback (try the
type's qualified_name, then loop over `("builtins", "tpy")`) gets
rewritten as a single `get_type_def` + one
`find_factory_by_simple_name` fallback. Small, but it was called
out by name in the phase intro.

#### Phase F.3f -- Residual parser typesys coupling cleanup (DONE)

Six sub-steps on `typesys-migration-phase-F3f`, each byte-identical
under `uv run pytest --force-exec` (2768 passed + 1 skipped, zero
diffs in `tests/cases/**/expected/`). Drove parser's typesys imports
from 15 symbols (post-F.3e) down to the 8 bookkeeping/walker items
the Phase F.3 intro explicitly permits (`FieldInfo`, `RecordInfo`,
`TypeRegistry`, `FunctionInfo`, `MethodSignature`, `ProtocolInfo`,
`TypeParamKind`, `LiteralValue`). All TpyType-construction and
value-singleton imports are gone; parser no longer imports
`NominalType`, primitive singletons, structural helpers, or string
utilities from `typesys`.

##### F.3f.1 -- Optional `return_type`, drop `VOID` (DONE)

`TpyFunction.return_type`, `MethodSignature.return_type`, and
`FunctionInfo.return_type` become `Optional[...]`. Parser emits
`None` when no annotation is provided instead of importing
`typesys.VOID` as a placeholder. Sema's `_resolve_pending_type_refs`
substitutes `VOID` via a new `_resolve_return_type` helper before
any downstream reader sees the field.

For the parse-time path (`_finalize_function_refs`, used by
`@builtin_decorator` stubs, nested defs, and `FragmentParser`),
`None` is substituted by routing a synthetic
`TpyTypeRef("None")` through the resolver (which maps `"None"` to
`VOID`). Keeps the same substitution semantics without a typesys
value import in parser.

The `@builtin_function` param placeholder (parser.py:2158)
similarly emits `TpyTypeRef("None")` rather than `VOID` -- keeps
the params-list type unchanged.

##### F.3f.2 -- Move `_infer_type_from_expr` success path to sema (DONE)

Parser always emits `TpyInferFromDefaultRef` for bare `name = expr`
class-body assignments; sema's field-resolution pass runs the
inferrer on `FieldInfo.default_expr`. This unifies the parse-time
success/failure paths (F.3d.4 already moved the failure case) and
removes parser's direct typesys construction of `BIGINT`/`FLOAT`/`STR`
primitives and `NominalType` for records.

The sema-side inferrer (`_infer_field_type_from_default`) mirrors the
old parser logic one-for-one, consuming `TpyExpr` rather than
`ast.expr`: `TpyIntLiteral` -> `BIGINT`, `TpyFloatLiteral` -> `FLOAT`,
`TpyStrLiteral` -> `STR`, `TpyCall` with fixed-int / `int` / `float`
name -> matching singleton, and `TpyCall` with a registered record
name -> `NominalType(name)`. Returns `None` on unknown forms; the
field-resolution loop raises the existing "Cannot infer type for
field 'X'" error only on genuine failure.

Deletes `parser._infer_type_from_expr` along with `BIGINT`, `FLOAT`,
`STR` imports from parser.py.

##### F.3f.3 -- Local `_FIXED_INT_NAMES` constant (DONE)

`_validate_const_default` and `_get_default_value` only need name
membership for fixed-int constructors; neither needed the `TpyType`
singletons. Parser gets a module-local `_FIXED_INT_NAMES` frozenset
kept in sync with `typesys.ALL_FIXED_INTS`, dropping the
`_FIXED_INT_MAP` import. The map itself stays in `type_resolver`
for the actual singleton lookup (bare-name resolution + sema's
field-default inferrer).

##### F.3f.4 -- Defer `ensure_qualified` on `cpp_concept` (DONE)

`@native("ConceptName")` on a protocol previously had its string
argument normalized at parse time via `typesys.ensure_qualified`
(adding the `::` prefix when the name isn't already qualified). The
raw string travels untouched from parser to sema's
`register_protocol`, so the normalization can happen at the copy
point into `ProtocolInfo` without changing any consumer behaviour
(`ensure_qualified` is idempotent on already-qualified names).

Deletes `ensure_qualified` from parser.py.

##### F.3f.5 -- Eliminate `NominalType` from parser (DONE)

Four independent `NominalType` call-site groups in parser.py each
get addressed:

- **5a.** `@builtin_type` method return-type fixup relocated from
  parser's post-registration block to sema. The fixup re-wraps
  `NominalType(name=class_name)` return types with
  `builtin_type_key` as `_module_qname`. Since F.3b.5, methods emit
  `TypeRefNode` (making the old parse-time `isinstance` check
  unreachable in practice); post-move suite run with an assertion at
  the fixup's branch showed zero hits across all 2768 tests, so the
  relocated block is deleted outright -- the resolver +
  `resolve_type`'s user-record substitution now mint `_module_qname`
  cleanly on their own.
- **5b.** `_schema_from_stub`'s
  `isinstance(ptype, NominalType) and ptype.qualified_name() ==
  qnames.TYPE` predicate replaced with
  `type_def_of(ptype).qname == qnames.TYPE`. `type_def_of` is the
  qname-based dispatch used elsewhere; this was one of the last
  isinstance stragglers.
- **5c.** Three `registry.register_enum(NominalType(name=X))` call
  sites (module-level enum, dotted nested enum, record-nested enum)
  use a new `TypeRegistry.register_enum_placeholder(name: str)`
  method. Parser-time enum name registration stays intact; the
  `NominalType` construction moves into `typesys` itself.
- **5d.** `FragmentParser._parse_type_annotation` override previously
  wrapped `super()._parse_type_annotation` in try/except and
  constructed `NominalType` placeholders for unresolved names in
  macro fragments. The fallback logic moves into
  `TypeResolver.resolve_lenient(ref, scope)`, which mirrors the
  former message-pattern filter (`"Unknown type"`, `"Unknown generic
  type"`, `"Unsupported qualified type"`) and recursively constructs
  `NominalType` placeholders for `TpyTypeRef` subtrees.
  `FragmentParser`'s override becomes a three-line walker + lenient
  resolver composition.

After all four, `NominalType` drops from parser.py's typesys import
block.

##### F.3f.6 -- Relocate `public_module_name` (DONE)

`public_module_name` is a pure string helper with no `TpyType`
dependencies. Relocating it to a new leaf module `tpyc/module_names.py`
lets parser, `parse/imports.py`, and sema modules import it without
routing through `typesys`. `typesys.py` still uses
`public_module_name` internally (for `register_protocol_module`) and
imports it back from `.module_names`. No circular: `module_names`
has zero tpyc dependencies.

#### Phase F.3g -- Thread per-record module provenance into parser.registry (DONE)

**Goal.** Parser populates `RecordInfo.module`,
`ProtocolInfo.module`, and enum placeholder qnames at class
registration, so the resolver / sema can read authoritative qname
info off `record_info.qualified_name()` (or the enum placeholder
singleton) without inferring module from context. Establishes
parser.registry as the canonical carrier of per-record module
provenance for *same-module* declarations.

**Not a goal (moved to Phase F.5).** Retire the three substitution
blocks in `sema/type_ops.py::resolve_type`. F.3g originally aimed
to delete them by also teaching the parser-side `TypeResolver` to
mint `_module_qname` on the first pass (attempted as F.3g.4, then
reverted). The approach works for same-module refs but fails for
cross-module imports through re-export facades -- `from tplib
import ArrayList` re-exported from `tplib.array_list` has surface
module `tplib` but defining module `tplib.array_list`. The
parser-side resolver is one-module wide; only sema's `ctx.registry`
(or the compiler's `_exports_to_module_info`) has the defining
module. Retiring the substitution requires reshaping the parser's
import table, which is its own concern -- see Phase F.5.

**Effect.** Parser populates `RecordInfo.module`,
`ProtocolInfo.module`, and enum placeholder qnames using
`public_module_name(self._imports._module_name,
self._directives.cpp_namespace)`. Entry-point modules use
`"__main__"` (matching sema's convention and Python's runtime
`__name__`) via a new `is_entry_point` flag threaded from the
compiler through `parser.parse()`. Sema's `register_enum` qname
construction is aligned to also use `public_module_name` so the
qname matches the parser-side placeholder in every case.

The three substitution blocks in `resolve_type` still run, but for
*every* path they now pull a fully-populated `RecordInfo` /
`ProtocolInfo` from sema's registry -- same-module (RecordInfo came
from this module's parser) and cross-module (RecordInfo copied
during sema's import pass with `defining_module` preserved).
`record_info.qualified_name()` is authoritative in both cases. The
comment block on `resolve_type` is rewritten to describe this
invariant directly; the stale pre-F.3 note ("...goes away entirely
once Phase F makes the parser emit TpyTypeRef instead of
NominalType") is replaced with the real rationale.

**Landed sub-steps:**

- **F.3g.1** plumbed directives into the parser loop
  (`_scan_directives` runs before `_parse_module`; `_directives` is
  stored on the parser; `_public_module()` helper returns
  `public_module_name(self._imports._module_name,
  self._directives.cpp_namespace)` -- `"__main__"` when
  `is_entry_point=True`).
- **F.3g.2** passes `module=self._public_module()` when constructing
  `RecordInfo` / `ProtocolInfo` at the four parser-side
  registration sites (top-level record/protocol, nested record,
  `_register_nested_types`).
- **F.3g.3** extended `register_enum_placeholder(name, module=None)`
  to mint `NominalType(name, _module_qname=f"{module or
  '__main__'}.{name}")` and threaded `is_entry_point` through
  `parser.parse()` + `compiler._discover_modules` so entry-point
  records/protocols/enums get `"__main__"` qnames consistently with
  sema. Sema `register_enum` was aligned to also route qname_module
  through `public_module_name`.

**Risks / watch-outs (resolved for this phase):**

- Parser and sema must agree on the qname for entry-point modules.
  Resolved by introducing `is_entry_point` (sema already used
  `"__main__"` for entry modules; parser now does the same).
- Builtin (`@builtin_type`) records: `qualified_name()` returns
  `builtin_type_key` when set, so module assignment is harmless for
  those; no special-casing needed.
- Nested / dotted records (e.g. `Outer.Inner`) also populate `module`
  at their registration site so the substitution block sees a
  RecordInfo with the right qname for `Outer.Inner`.

### Phase F.4 -- Fold record traits onto TypeDef (DONE)

Three process-global sets in `typesys.py` carried per-record
boolean traits that already had a natural home on `TypeDef.record`
(populated since Phase E):

- `_value_type_record_names: set[str]` -- records implementing `ValueType`.
- `_send_record_names: set[str]` -- records marked `Send`.
- `_sync_record_names: set[str]` -- records marked `Sync`.

**Goal (achieved).** The three sets and their registration helpers
are deleted; the booleans now live on `RecordInfo` (next to the
pre-existing `is_value_type` field). `NominalType.is_value_type` /
`is_send` / `is_sync` read them off `type_def_of(t).record`, one
source of truth per qname, matching the pattern Phases B/D/E
established for containers, primitives, and enums.

**Landed sub-steps:**

- **F.4.1** added `is_send: bool = False` and `is_sync: bool = False`
  fields on `RecordInfo` alongside the pre-existing `is_value_type`.
  `validate_record_inheritance` in `sema/registration.py` now writes
  the RecordInfo fields in addition to calling the old
  `register_*_record` helpers (dual-write; purely additive).
- **F.4.2** flipped `NominalType.is_value_type` / `is_send` /
  `is_sync` to consult `td.record.is_*` as the per-record fallback
  after the existing container/primitive dispatch, replacing the
  `self.name in _*_record_names` short-name match. Three unit tests
  in `tpyc/test_compiler.py::TestSendSyncRecordDerivation` that had
  constructed bare `NominalType("Point")` / `NominalType("Holder")` /
  `NominalType("Container")` without `_module_qname` were updated to
  carry `_module_qname="__main__.<Name>"` so that `type_def_of`
  resolves -- the architecturally correct shape for probing post-sema
  state (short-name lookup was an accident of the deleted set).
- **F.4.3** deleted `_value_type_record_names` / `_send_record_names`
  / `_sync_record_names`, the `register_value_type_record` /
  `register_send_record` / `register_sync_record` helpers, and their
  entries in `clear_all_compilation_state()` /
  `clear_codegen_state()`'s docstring. Dropped the three imports in
  `sema/registration.py`. Updated `conftest.py` docstring and the
  TODO.md process-globals bullet.

**Why separate from F.3.** F.3 focused on parser/sema convergence
(parser emits TpyTypeRef; sema owns resolution). F.4 completed the
record-trait consolidation that Phase E started on the
container/primitive side -- different axis, independent commit.

Full suite green (`uv run pytest`: 2773 passed, 1 skipped); byte-identical
codegen verification via `--force-exec` at the phase boundary.

### Phase F.5 -- Canonicalize import sources to defining modules (PLANNED)

Independent of the F.3 parser-refactor thread; lives here because it
unlocks retiring the three residual substitution blocks in
`sema/type_ops.py::resolve_type` that F.3g couldn't delete.

The blocks survive only because the parser-side `TypeResolver` can't
compute cross-module qnames for re-export facades. Specifically,
`parser._imports.get_import_source(name)` returns the **surface**
module -- the one named in the `from X import Y` statement -- while
the authoritative `TypeDef` qname uses the **defining** module:
`from tplib import ArrayList` resolves through `tplib/__init__.py`'s
`from .array_list import ArrayList` so the defining module is
`tplib.array_list`, not `tplib`.

Sema's `ctx.registry` already has the right answer: its import pass
copies each imported `RecordInfo` / `ProtocolInfo` / `NominalType`
into the importing module's registry with `defining_module`
preserved (so `record_info.qualified_name()` is authoritative). The
compiler also has the right answer: `_exports_to_module_info`
resolves re-exports when building each module's `ModuleInfo` for
cross-module consumption. Only the parser lacks it, because parser
runs per-module before the compiler has finished aggregating.

**Goal.** Expose the defining module to parser's import table so
that `parser._imports.get_import_source(name)` returns
`(defining_module, original_name)` uniformly, and the resolver can
mint canonical `_module_qname` on the first pass for every import
-- facade or not. The three substitution blocks (enum / user-record
/ protocol-qname) in `resolve_type` then become dead code and get
deleted. The "resolver emits qname-bearing NominalType" approach
F.3g.4 attempted can be reattempted and landed cleanly on top of
this.

**Why.** Removes the last parser/sema boundary patch in the resolve
pipeline. Moves the "one source of truth per qname" discipline into
the import-resolution layer, where it belongs. Modest perf win on
`resolve_type` (it runs on every `TpyType` traversal -- the
substitution check is one of several branches that fire per call).

**Why separate from F.3.** F.3 was about reshaping the
parser/sema boundary for type-reference nodes -- parser output
structure. F.5 is about reshaping the compiler's import table --
cross-module aggregation. Different axis, different layer, ships
independently.

**Dependencies.** F.3g.1-3 (parser.registry populated with
RecordInfo.module / ProtocolInfo.module / enum placeholder qnames).
No dependency on F.4 or G.

**Sketch.**
1. After `compiler._compute_compile_order()` (so all modules are
   parsed) but before `_analyze_module` loops start, walk each
   compiled module's `parser._imports._name_index` and rewrite
   entries: `(surface_module, original_name)` ->
   `(defining_module, original_name)` wherever the surface module's
   `ModuleInfo` shows the name was re-exported. For the common
   non-facade case (`from mylib import Foo` where mylib defines Foo
   directly) the tuple is unchanged.
2. Use the compiler's existing re-export tracking
   (`_exports_to_module_info` already knows defining modules --
   expose it as a lookup per `(surface_module, name)` if not
   already).
3. Re-land F.3g.4's resolver-side qname minting:
   `type_resolver._resolve_registered_type` and `_resolve_ref`'s
   generic branch mint `_module_qname = f"{module}.{original}"`
   from the now-canonical import tuple on the first pass.
4. Delete the three substitution blocks in
   `sema/type_ops.py::resolve_type`. Update the comment block.
5. Byte-identical codegen verification under `--force-exec`.

**Risks / watch-outs.**
- Chained re-exports (`pkg/__init__.py` re-exports from
  `pkg.sub/__init__.py` which re-exports from `pkg.sub.mod`): the
  lookup must resolve transitively to the ultimate defining module,
  not one step up. The compiler's re-export tracking already walks
  chains; sketch step 2 just exposes it -- audit first.
- Aliased re-exports (`from .colors import Color as C`): the
  `original_name` piece of the tuple must be the *defining-module*
  name (`Color`), not the local alias (`C`). Compiler state should
  already have this but check.
- Same-module forward references don't go through the import table;
  the resolver still needs its `parser._module_class_names` /
  `parser.registry.get_record` path for those (F.3g.4 covered both
  branches). The re-land should keep the dual lookup.

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
