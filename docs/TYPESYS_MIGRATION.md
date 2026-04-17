# Type System Migration Plan

## Goal

Unify the type hierarchy around two shapes: **nominal** (identity = qualified name + type args, behavior from a registry) and **structural** (identity = operand shape, behavior derived from operands). Builtins, containers, primitives, records, protocols, and enums all become `NominalType` with behavior sourced from a single `TypeDef` registry. Wrappers (`Ptr`, `Own`, `Optional`, `Union`, `Tuple`, `Callable`, `Readonly`) stay structural.

## Current state (Phases A, B, and C complete)

```
TpyType (frozen dataclass base)
  NominalType (name, type_args)
    -- all 11 container subclasses removed; containers now flow through
       NominalType + TypeDef registry with qname-based dispatch.
  FixedIntType, Int32Type, BoolType, StrType, CharType, FloatType, ...     (~20 primitive subclasses -- TODO Phase D)
  EnumType, IntEnumType                                                     (distinct data model -- TODO Phase E)
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

**Note on conformance tests:** the Phase A conformance tests in
`tpyc/test_type_def_registry.py` compared TypeDef answers against
subclass methods. Post-Phase B, those subclasses are gone, so the
asserts are now tautological (both sides read from the registry). For
Phase D, reintroduce independent validation *before* deleting the
primitive subclasses: either compute expected values from the old
subclass's method source (copy the logic into the conformance test and
compare against `make_int32(...).is_send()` etc.), or snapshot the
current answers into a hard-coded table in the conformance test. Do
this in Phase D step 0, before any subclass deletion.


Primitive subclasses (`FixedIntType`, `BoolType`, `StrType`, `CharType`, `FloatType`, per-width aliases) carry behavior: arithmetic rules, coercion, range, `to_cpp`. Move all of it to TypeDef:

1. Enrich TypeDef with `int_traits`, `float_traits`, `char_traits`, `str_traits` as needed.
2. Migrate arithmetic lattice, coercion, and range-tracking to read TypeDef via qname dispatch instead of `isinstance(x, FixedIntType)` etc.
3. Replace primitive `to_cpp()` implementations with `TypeDef.cpp_formatter`.
4. Construct primitives as `NominalType("Int32", ())` + TypeDef entry; delete the primitive subclasses.
5. `TypeKind` categories (`FIXED_INT`, `FLOAT`, ...) either shrink to structural-only or become a derived property reading `TypeDef.category`. Decide during implementation.

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
