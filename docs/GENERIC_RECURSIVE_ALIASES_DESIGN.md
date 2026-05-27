# Generic Recursive Type Aliases -- Design

Extending TPy's existing non-generic recursive type aliases (D19, shipped)
to support type parameters.

```python
# Today (shipped):
type JsonValue = str | float | bool | None | list[JsonValue] | dict[str, JsonValue]

# Goal:
type Tree[T] = T | list[Tree[T]]
type Result[T, E] = T | E
type DictTree[K, V] = V | dict[K, DictTree[K, V]]
```

## Status

**Phase 1 shipped (2026-05).** Generic non-recursive aliases (`type Pair[T] =
tuple[T, T]`, multi-param, alias-in-alias, cross-module) work end-to-end via
substitution at parse-resolution. Generic recursive aliases stay rejected with
a clean diagnostic; Phase 2 will land the templated wrapper struct.

Design revision 2 (incorporates Codex review + independent review) restructured
around the principle that **only recursive aliases have C++-level nominal
identity** (a generated wrapper struct); non-recursive generic aliases are pure
sema-only expansion.

## Goals (v1)

- Generic non-recursive aliases: `type Pair[T] = tuple[T, T]`.
- Generic recursive aliases: `type Tree[T] = T | list[Tree[T]]`.
- Multi-parameter aliases: `type Pair[K, V] = tuple[K, V] | ...`.
- Cross-module: alias defined in module A, used in module B.
- `isinstance(x, AliasName)` (bare name) works on the **recursive** form
  only. Non-recursive aliases reject `isinstance` -- they have no
  runtime identity.
- Generated C++ uses one `template<...> struct AliasName { ... };` per
  **recursive** alias; non-recursive aliases produce no C++ declaration
  (sema expands them at use sites).

## Non-goals (v1) -- captured for future revisit

- **Bounds on alias type params** (`type Tree[T: Hashable] = ...`).
  Parser rejects the PEP 695 bound syntax with "bounds on alias type
  params not yet supported." Storage shape leaves room for them so
  adding bounds later is a sema-only change.
- **Mutual recursion across generic aliases / generic alias + generic
  class** (`type Expr[T] = Lit[T] | Op[T]` where `Lit[T]` / `Op[T]`
  contain `Box[Expr[T]]` fields). Out of scope. The validator must
  detect both direct and transitive cycles (e.g. `type Forest[T] =
  list[Tree[T]]; type Tree[T] = T | Forest[T]`) and emit a distinct
  "mutual recursion across generic aliases not supported in v1"
  diagnostic.
- **Parameterized `isinstance`** (`isinstance(x, Tree[Int32])`). Bare
  alias name only.
- **Conflicting type args at recursive positions** (`type Bad[T] = T |
  list[Bad[int]]`). Rejected at sema. Framed honestly: v1 only
  supports identity recursion to avoid template-graph termination
  analysis; non-growing constant args (`Bad[int]`) are in fact
  terminating but rejecting them is the simplest defensible rule.

## Design decisions (Q1-Q8 from design discussion)

| # | Decision |
|---|---|
| Q1 | Template-based monomorphization (one `template<...> struct` emission per recursive alias name; C++ instantiates per use) |
| Q2 | No bounds in v1; parser rejects bound syntax explicitly; storage shape extensible |
| Q3 | Multi-param + same-args invariant in v1 |
| Q4 | Mutual recursion across generic aliases deferred (validator detects + diagnoses) |
| Q5 | `isinstance(x, AliasName)` accepts only the bare alias name; non-recursive aliases reject `isinstance` outright (no runtime identity) |
| Q6 | Conflicting type args at recursive positions rejected in v1 (identity-recursion-only rule) |
| Q7 | Two phases: Phase 1 generic non-recursive (sema-only); Phase 2 generic recursive (adds C++ wrapper) |
| Q8 | `TypeAliasInfo` dataclass in `typesys.py` used by sema + exports; parser keeps a tuple shape with added type-param fields |

---

## Architectural principle: recursive vs non-recursive split

Two distinct mechanisms with one shared piece of plumbing:

| Aspect | Non-recursive generic alias | Recursive generic alias |
|---|---|---|
| Sema use-site resolution | Substitute body; return expanded type | Return `NominalType("Tree", [args])` |
| C++ identity | None -- alias does not exist in C++ | `template<...> struct Tree { variant<...> value; };` |
| `isinstance(x, Name)` | **Rejected** at sema (no runtime identity) | Folds to True via wrapper's variant tag |
| Member expansion | Eager at sema use-site | On-demand for narrowing / match / codegen internals |
| Cross-module emission | No C++ artifact | Wrapper struct in defining module's header |

This split resolves the internal contradiction in the previous revision
(which conflated "emit `using` template" with "substitute at use sites").
Phase 1 owns the non-recursive column; Phase 2 owns the recursive column.

---

## Storage shape (foundation for both phases)

### `TypeAliasInfo` dataclass (in `typesys.py`)

```python
@dataclass
class TypeAliasInfo:
    name: str
    body: TpyType                          # fully resolved in defining module's context
    type_params: list[str] = field(default_factory=list)
    type_param_kinds: list[TypeParamKind] = field(default_factory=list)
    type_param_bounds: dict[str, TpyType] = field(default_factory=dict)  # empty in v1
    defining_module: str | None = None     # qualified module name; None for module-local
    is_recursive: bool = False             # set by recursive-union detection
    loc: SourceLocation | None = None
```

Lives in `typesys.py` because it's a type-system concept used by sema
+ exports. Codegen consumes it via the same channels it consumes other
type info (no new codegen dependency on a sema-internal shape).

`defining_module` resolves Codex review point E: when module B uses
`Pair[Int32, Str]` (defined in A), the body's `TypeParamRef`s and
internal `NominalType` references are resolved in A's context at A's
parse/sema time, before export. By the time B sees `TypeAliasInfo`,
the body is fully qualified -- B substitutes args by walking and
replacing `TypeParamRef`s, no resolution required. The
`defining_module` field is there for diagnostics ("Pair is defined in
module A") rather than load-bearing for correctness.

### Migration scope

- `tpyc/typesys.py:4082` -- `ModuleInfo.type_aliases: dict[str, TpyType]`
  becomes `dict[str, TypeAliasInfo]`. Sema's primary view.
- `tpyc/compiler.py:947` -- `ModuleExports.type_aliases` migrates to the
  same shape. Cross-module wiring.
- `tpyc/parse/nodes.py:1468` --
  `TpyModule.type_aliases: dict[str, tuple[TpyType | TypeRefNode, SourceLocation]]`
  gets two extra elements (`type_params` + `type_param_kinds`), kept as
  a tuple. The parser shape carries `TypeRefNode` placeholders until
  `resolve_refs` runs; converting it to a full dataclass at parse time
  is unnecessary churn. Sema builds `TypeAliasInfo` after resolution.
- ~10 read sites in `parse/`, `sema/`, `codegen_cpp/`, `compiler.py`
  migrate to the new shape.

One-time refactor at the start of Phase 1, single reviewable commit.

---

## Layering fix (Codex review point A)

`substitute_type_params` currently exists twice:
- `tpyc/codegen_cpp/types.py:257` -- the version the previous plan revision
  called from sema (wrong direction).
- `tpyc/sema/type_ops.py:534` -- the right home.

Phase 1's sema substitution uses the `sema/type_ops.py` version. If the
two implementations diverge in subtle ways, extract a single shared
implementation to `typesys.py` and have both wrappers call it. Codegen
must not be imported from sema; sema may be imported from codegen.

---

## Phase 1: Generic non-recursive aliases (sema-only)

Pure sema work. Generic aliases become a sema-time substitution; C++
sees the expanded body and never the alias name.

### Parser changes (`tpyc/parse/parser.py`)

PEP 695 `ast.TypeAlias` carries `type_params` (Python 3.12+). The parser
must:

1. Read `node.type_params` in `_register_type_alias` (currently ignores
   them -- silent type-param loss is today's failure mode).
2. Extract `(name, kind)` per param the same way `TpyRecord` parsing
   does (parser.py ~1368-1387).
3. **Reject bound syntax explicitly**: PEP 695 allows `type Tree[T:
   Hashable] = ...`. v1 emits "bounds on alias type params are not yet
   supported; remove the bound or open an issue." Silent stripping is
   a footgun.
4. Store `type_params` + `type_param_kinds` in the parser's tuple shape
   on `TpyModule.type_aliases`.

`Foo = ...` (older alias-via-assignment) stays non-generic only -- the
assignment syntax has no type-param surface.

### Sema substitution at use sites

When sema encounters `Pair[Int32, Str]` (a `NominalType("Pair", [Int32,
Str])` resolved against the alias registry):

1. Look up `Pair` in `module.type_aliases` (or imported `ModuleInfo`).
2. Validate arity. Error: `"Type alias 'Pair' takes 2 type arguments,
   got 1"`.
3. **If `info.is_recursive` is False**: build `subst = {T_i:
   args[i]}`, return `substitute_type_params(info.body, subst)`. The
   expanded type flows downstream; the alias name disappears from
   sema's view.
4. **If `info.is_recursive` is True**: see Phase 2 below.

Validate arity at every use site, including alias-in-alias composition
(`type A[T] = Pair[T, T]` -- nested substitution must terminate; v1's
identity-recursion-only rule means nested non-recursive composition
terminates trivially).

### `isinstance(x, Pair)` rejection (Codex review point B)

Non-recursive generic aliases have no runtime identity (`Pair[T]` is
just `tuple[T, T]` -- there's no Pair-ness at runtime). Sema rejects
`isinstance(x, Pair)` with: `"isinstance on generic alias 'Pair' is
not supported. Use isinstance on the expanded type members instead
(e.g. isinstance(x, tuple))."`

This rejection sites in `tpyc/sema/calls.py:_analyze_isinstance` and
parallel positions. Diagnostic should distinguish from the existing
parameterized-isinstance error (Q5).

### Codegen: nothing to do

Sema expands all non-recursive aliases at use sites. Codegen never
sees `Pair`; it sees the substituted body. No `using` template, no
forward decls, no cross-module wiring. Phase 1 ships without touching
`codegen_cpp/`.

### Cross-module

`ModuleExports.type_aliases` carries `TypeAliasInfo` shape. Importing
modules look up by name + substitute at the use site, using the
already-resolved body from the defining module.

### Tests (Phase 1)

New cases under `tests/cases/union/`:

- `generic_pair_basic`: `type Pair[T] = tuple[T, T]`; declare, pass to
  function, return, unpack.
- `generic_result`: `type Result[T, E] = T | E`; narrow via
  `isinstance` on the expanded members (not on `Result` itself).
- `generic_alias_arity_error`: `Pair[Int32]` when `Pair` takes two
  params -- arity error.
- `generic_alias_in_alias`: `type A[T] = Pair[T, T]` -- composition
  through substitution.
- `generic_alias_cross_module`: alias defined in module A, used in
  module B with concrete type args.
- `generic_alias_in_container`: `list[Pair[Int32, Str]]` -- field /
  param / return positions.
- `error_isinstance_bare_generic_alias`: `isinstance(x, Pair)` --
  expect "no runtime identity" error.
- `error_isinstance_parameterized`: `isinstance(x, Pair[Int32, Str])`
  -- expect "use the bare alias name" error (existing recursive-form
  diagnostic).
- `error_alias_bound_syntax`: `type Tree[T: Hashable] = ...` -- expect
  "bounds not yet supported" parser error.

All existing non-generic alias tests must keep passing -- they use
empty `type_params` and migrate automatically.

---

## Phase 2: Generic recursive aliases (adds C++ nominal wrapper)

Builds on Phase 1's storage + substitution. Adds: same-args validator,
templated wrapper struct, on-demand member expansion for narrowing /
match.

### Carry-overs from the master merge (must address in Phase 2)

Surfaced when Phase 1 was rebased onto master's unified recursive-union
representation (`AliasRef` placeholder + `union_wrapper_index` +
`needs_wrapper()`). Both are benign in Phase 1 (generic-recursive
aliases are rejected outright) but become load-bearing once Phase 2
stops rejecting them:

1. **Self-reference type args are dropped.** Phase 1's generic-form
   self-ref placeholder (`tpyc/parse/type_resolver.py`, the
   `name == self._pending_alias` branch) emits a bare
   `AliasRef(name, module=...)` -- master's `AliasRef` carries no type
   args, and Phase 1 doesn't need them (the alias is rejected). Phase 2's
   wrapper-struct emission *does* need to know what the recursive
   position was instantiated with (`Tree[T]` vs a malformed
   `Tree[T, U]`). Phase 2 must either extend `AliasRef` with an args
   tuple or recover the args from the alias's declared `type_params` at
   the recursion site, and add the arity check that the bare-AliasRef
   path currently skips. Dropping the args also means a malformed
   self-ref arg (`list[Tree[Undefined]]`) no longer reports "Unknown
   type" -- restore that diagnostic when the args are resolved again.

2. **The generic-recursive rejection fires late.** Phase 1's rejection
   lives in the alias-registration loop in `_register_type_aliases`,
   which runs *after* `_detect_recursive_unions`,
   `_register_union_wrappers`, `_fix_recursive_optional_annotations`,
   and `_validate_recursive_union_paths`. For a generic recursive alias
   with a union body, those passes briefly register
   `TypeParamRef`-keyed entries in the compiler-wide
   `union_wrapper_index` / `alias_by_members` before the `SemanticError`
   aborts. Harmless while the abort discards everything, but Phase 2
   removes the rejection -- at which point either (a) those passes must
   correctly handle the parameterized wrapper (the intended design), or
   (b) any interim partial-support state must not leak a
   `TypeParamRef`-keyed wrapper into a concrete union of the same shape.
   Decide the ordering deliberately when the gate comes out.

### Same-args invariant validator

Today's `validate_recursive_union_paths` in
`tpyc/sema/cycle_detection.py` walks the union body checking
self-references go through a container. Phase 2 extends:

1. **Direct self-reference check**: at each `NominalType("Tree",
   args)` inside `Tree`'s body where args matches alias type params
   in position:
   - Arity must match.
   - Each arg must be a bare `TypeParamRef` matching the alias param
     at that position (`Tree[T]` requires `T`; `Pair[K, V]` requires
     `K, V` in order).

2. **Transitive cycle detection** (Codex review point J): the validator
   walks NominalType references in the body. If a reference is to
   another generic alias (`Forest`) whose body transitively references
   the current alias (`Tree`), that's mutual recursion across generic
   aliases -- emit a distinct diagnostic: `"alias 'Tree' transitively
   recurses through 'Forest'; mutual recursion across generic aliases
   is not supported in v1"`.

3. **Conflicting recursive args** (`type Bad[T] = T | list[Bad[int]]`,
   `type Pair[K, V] = ... | dict[V, Pair[V, K]]`): caught by check (1)
   -- the recursive position's args aren't bare `TypeParamRef`s
   matching positionally. Diagnostic: `"recursive position of alias
   'Bad' must reuse type parameter 'T' at position 0; got 'int'. v1
   supports identity recursion only."`

4. **Mark the alias**: on success, set `TypeAliasInfo.is_recursive =
   True`. This drives Phase 2's branch in sema use-site resolution
   (see below).

### Sema use-site resolution for recursive aliases

When sema encounters `Tree[Int32]` and `info.is_recursive`:

1. Validate arity.
2. Return `NominalType("Tree", [Int32])`. **Do not substitute the body.**
3. Body member expansion happens on-demand:
   - **Narrowing** (`if isinstance(x, list):`): walk
     `info.body.members`, substitute `{T_i: args[i]}`, find the
     matching member by structural match, narrow `x` to the
     substituted member.
   - **Match arm matching**: same -- expand members on demand,
     dispatch by variant index.
   - **Codegen rendering**: emit `Tree<int32_t>` at the use site; the
     wrapper template handles the rest.

This mirrors how generic records resolve at use sites (`Stack[Int32]`
becomes `NominalType("Stack", [Int32])`, members on-demand).

### Wrapper struct templating

`tpyc/codegen_cpp/generator.py:_gen_recursive_union_struct` (line
1541) currently emits:

```cpp
struct JsonValue {
    using variant_type = std::variant<...>;
    variant_type value;
    JsonValue() = default;
    template<typename T>
        requires std::constructible_from<variant_type, T&&>
    JsonValue(T&& v) : value(std::forward<T>(v)) {}
    bool operator==(const JsonValue&) const = default;
    friend std::ostream& operator<<(std::ostream& os, const JsonValue& v) { ... }
};
```

Phase 2 changes (when `info.type_params != []`):

1. Prepend `template<TypeParamKindCpp T1, TypeParamKindCpp T2, ...>`
   to the struct declaration. Each `TypeParamKindCpp` is `typename` for
   `TYPE` kind, `int` (or whatever the kind requires) for `INT` kind.
2. The forwarding ctor's template parameter is renamed to a sentinel
   `_TpyAliasCtorArg` (Codex point H / mine point I): cannot collide
   with any user-supplied `T`, `U`, `K`, `V`, etc. Naming convention
   matches existing TPy-internal C++ identifiers (underscore prefix
   used for compiler-emitted names already).
3. **Constrain `operator==`** (Codex point H / mine point 2): emit
   ```cpp
   bool operator==(const Tree&) const
       requires std::equality_comparable<variant_type> = default;
   ```
   Tree instantiations with non-equality-comparable T silently lose
   `==`; user gets a clean C++ error at the first comparison site.
   Alternative considered (unconditional `= default`) emits the
   ctor-body error which is acceptable but worse UX.
4. **Default ctor policy**: today's `Tree() = default;` only works if
   the variant's first alternative is default-constructible. For
   `Tree[T] = T | list[Tree[T]]` with non-default-constructible T,
   the default ctor of `Tree<T>` becomes ill-formed. Emit:
   ```cpp
   Tree() requires std::default_initializable<variant_type> = default;
   ```
   Same pattern as `operator==`. User loses the default ctor when T
   doesn't support it; explicit construction still works.
5. **`operator<<` friend stays inline** (declared inside the template
   body); the existing `tpy::detail::print_element` dispatch handles
   variant printing per-alternative. No ODR concern because the friend
   is per-instantiation.
6. Forward declarations at `generator.py:910-912` add `template<...>
   struct AliasName;`.

### Match / narrowing details (Codex point F)

`case T() as leaf` (where T is the alias type param) is **NOT**
supported in v1 -- type-param-as-class-pattern doesn't have a clear
semantics. Match through a recursive generic alias uses:

```python
def walk[T](t: Tree[T]) -> Int32:
    match t:
        case list() as branches:
            return sum(walk(b) for b in branches)
        case _:                       # the T alternative
            return 1
```

Or, if narrowing to the T alternative is needed:

```python
if isinstance(t, list):
    ...
else:
    # t is the T alternative; bind via assignment
    leaf: T = t
```

The `case _` arm is the catch-all for the T alternative until
type-param-as-pattern is a separate feature. Tests must use these
patterns, not `case T()`.

### Registry adaptation (Codex point L commitment / mine #6)

`recursive_union_names: set[str]` stays name-keyed. The wrapper is one
template per alias name, so name-keyed membership is correct.

`is_recursive_union` reverse-map (frozenset-of-members -> name) in
`sema/context.py:680`: **for generic recursive aliases, every reference
is parameterized (`Tree[Int32]`)**, never raw expanded variant. The
reverse-map is queried only when sema sees a raw `UnionType` and wants
to know "is this a recursive alias's body?" -- which doesn't happen
for generic aliases (their bodies live behind the templated wrapper).

**Commitment**: leave the reverse-map non-generic-only. Generic-alias
recognition uses `info.is_recursive` on the looked-up `TypeAliasInfo`,
which is the natural signal. Document this in the code comment at the
reverse-map's construction site.

### Cross-module generic recursive aliases

Wrapper struct is emitted in the defining module's header, with
`template<typename ...>` prefix. Importing modules see the template
via include. Forward decls at the include-cycle break points add the
templated prefix.

**Codex point E concern**: `TypeAliasInfo.body` is resolved in the
defining module's context at parse + sema time, before export. Body's
`TypeParamRef`s are bare names; body's `NominalType` references to
other types are module-qualified (via the existing resolve pipeline).
Importing modules walk the body to substitute type params at use sites
-- no resolution context needed because the body is already fully
qualified. The `defining_module` field on `TypeAliasInfo` is used only
for diagnostics, not load-bearing.

### Tests (Phase 2)

Under `tests/cases/union/`:

- `generic_recursive_tree_int`: `type Tree[T] = T | list[Tree[T]]`;
  construct, pass, match via `case list()` + `case _`.
- `generic_recursive_tree_str`: same alias, instantiated with `Str`.
- `generic_recursive_tree_nested`: `Tree[list[Int32]]` -- alias type
  arg is a container.
- `generic_recursive_two_param`: `type DictTree[K, V] = V | dict[K,
  DictTree[K, V]]`.
- `generic_recursive_box_field` (mine point N): `class Holder[T]:
  data: Box[Tree[T]]` -- pins generic alias inside Box inside generic
  class.
- `generic_recursive_cross_module`: alias in module A, used in module
  B with concrete type args, match dispatch.
- `generic_recursive_non_eq_t`: `Tree[T]` where T is a class without
  `__eq__` -- pins the constrained `operator==`; `t1 == t2` errors
  cleanly.
- `error_recursive_arity_mismatch`: `type Tree[T] = T | list[Tree[T,
  T]]` -- arity error.
- `error_recursive_non_identity_arg`: `type Weird[T] = T |
  list[Weird[list[T]]]` -- "must reuse type parameter" (growing-args
  case).
- `error_recursive_swap_args`: `type Pair[K, V] = ... | dict[V,
  Pair[V, K]]` -- "must reuse type parameter" (swapped-args case).
- `error_recursive_concrete_arg`: `type Bad[T] = T | list[Bad[int]]`
  -- "must reuse type parameter" (constant-args case, Q6 framing).
- `error_mutual_generic_recursion`: `type Forest[T] = list[Tree[T]];
  type Tree[T] = T | Forest[T]` -- "mutual recursion across generic
  aliases" (transitive cycle, Codex point J).

All existing non-generic recursive alias tests must keep passing
unchanged.

---

## Implementation order

### Phase 1 commits (shipped)

1. **`TypeAliasInfo` storage refactor (done)** -- introduced the dataclass
   in `typesys.py`, migrated `ModuleInfo.type_aliases` and
   `ModuleExports.type_aliases`. `TpyModule.type_aliases` tuple extended with
   `type_params` + `type_param_kinds` elements. ~10 read sites migrated.
2. **Parser support (done)** -- `_parse_alias_type_params` reads
   `node.type_params` on `ast.TypeAlias`, rejects PEP 695 bounds /
   TypeVarTuple / ParamSpec / defaults. Type-param scope wired during alias
   body parsing.
3. **Sema substitution at use sites (done)** -- substitution happens at
   parse-resolution (mirrors how non-generic aliases already worked), via
   `substitute_type_params_structural` from `typesys.py` (correct layering --
   sema doesn't depend on codegen). `Pair[Int32, Str]` substitutes to
   `tuple[Int32, Str]`.
4. **Reach + container + field support (done)** -- generic non-recursive
   aliases work as field types, param types, return types, container element
   types. `_lookup_generic_alias_info` unifies local / short-import /
   dotted-qualified lookup. Codex-flavored isinstance diagnostic for
   `isinstance(x, BareGenericAlias)`.
5. **Cross-module wiring (done)** -- subsumed into step 4 via the unified
   lookup helper.
6. **Phase 1 tests (done)** -- 9 cases under `tests/cases/union/`:
   `generic_alias_pair_basic`, `_result_multi_param`, `_in_alias`,
   `_in_container`, `_cross_module`, `error_generic_alias_arity`, `_bound`,
   `_isinstance`, `_recursive`.

Post-/tpy-review fixes (same Phase 1 landing): widened non-union recursion
detection (`type Bag[T] = list[Bag[T]]` now rejects cleanly); synced
`is_recursive` flag at parse-resolve time so the parse-gate and sema-gate
agree; backfilled `# tpyc: type(...)` annotations on happy-path tests.

### Phase 2 commits (in order)

7. **`is_recursive` detection + same-args validator** -- extend
   `validate_recursive_union_paths` with the identity-recursion check
   and the transitive-cycle detector. Set
   `TypeAliasInfo.is_recursive`.
8. **Sema use-site resolution split** -- for recursive aliases,
   return `NominalType("Tree", [args])` instead of expanding the body.
   On-demand member expansion utility for narrowing + match consumers.
9. **Wrapper struct templating** -- update
   `_gen_recursive_union_struct` to emit `template<typename ...>`,
   sentinel ctor template param, constrained `operator==`, constrained
   default ctor.
10. **Cross-module template forward decl ordering** -- forward decls
    at `generator.py:910-912` get the templated prefix; verify include
    order machinery handles templated cycle-peer forward decls.
11. **Match + narrowing through templated wrapper** -- verify variant
    dispatch works through the template parameter.
12. **Phase 2 tests** -- ship alongside.

Each step is one commit on a feature branch; review at the end of
each phase before merging.

---

## Risks

- **Sentinel ctor template param**: `_TpyAliasCtorArg` chosen to be
  ugly and obviously compiler-emitted. If a user-supplied bound or
  alias name ever shadows it (unlikely), error message points at the
  sentinel name and we add an explicit reservation. Mitigation: a
  parse-time check that no user type-param starts with `_TpyAlias`.
- **Constrained auto-default machinery**: `operator==` and default
  ctor now have `requires` clauses. C++ overload resolution behavior
  when these constraints aren't satisfied differs subtly from "method
  doesn't exist" vs "method exists but unusable." Verify with
  `error_recursive_non_eq_t` test that the error site is meaningful
  (at the comparison/construction site, not at template
  instantiation).
- **Transitive-cycle detection performance**: the validator must walk
  every NominalType in every generic alias body. For N generic
  aliases with average body size B, naive is O(N * B * N) (each
  reference triggers a walk through the referenced alias). Mitigation:
  memoize "does alias X reach alias Y?" relation; amortize O(N^2) +
  O(N * B). Tractable for any realistic codebase.
- **Match through templated wrapper**: variant `index()` is template-
  param-agnostic, so dispatch should work. Mitigation: the
  `generic_recursive_two_param` test pins multi-instantiation
  dispatch.
- **Header organization for cross-module recursive generic aliases**:
  templates must be in the header. Mitigation: today's
  `_gen_recursive_union_struct` always emits to `.hpp`; templated
  variant inherits the same placement.

## Effort

- Phase 1: M (1-2 weeks). Storage refactor (mechanical) + parser +
  sema substitution + tests. No codegen.
- Phase 2: M-L (~2 weeks). Wrapper templating + validator extensions
  + on-demand member expansion + cross-module template forward decls.

(Previous revision estimated Phase 2 at M; revised to M-L because
templated `operator==` / default ctor constraint design and
transitive-cycle detection add real implementation surface.)

## Open follow-ups (post-v1)

Captured here so they don't get lost:

- **Bounds on alias type params** (Q2 deferral). `type Tree[T:
  Hashable] = ...`. Sema-only change once storage carries
  `type_param_bounds`.
- **Mutual recursion across generic aliases** (Q4 deferral). `type
  Expr[T] = Lit[T] | Op[T]` + `class Lit[T]: value: Box[Expr[T]]`.
  Requires threading alias type params through cross-type cycle
  topology.
- **Parameterized `isinstance`** (Q5 deferral). Carries no runtime
  benefit without RTTI.
- **Relaxing the same-args invariant** (Q6 deferral). Permitting
  constant-args (e.g. `Bad[int]` inside `Bad[T]`) needs a termination
  check at recursive positions. Only worth it if a real use case
  emerges.
- **Type-param-as-class-pattern in match** (Phase 2 carve-out). `case
  T() as leaf` would need a new pattern-match feature. Until then,
  use `case _` for the T alternative.
- **Diagnostic hint for silently-lost `node.type_params`**: today's
  parser drops alias type params. Once Phase 1 picks them up, this
  resolves. Pre-Phase-1, downstream errors about undefined `T` /
  `K` etc. inside an alias body could hint "did you mean to declare
  this alias as `type Foo[T] = ...`?" -- nice-to-have polish.

## References

- `docs/FEATURE_ROADMAP.md` -- D19 "Recursive Type Aliases" Done; this
  work is the listed D19 extension.
- `docs/LANGUAGE_FEATURES.md` -- update when shipped.
- `tpyc/parse/nodes.py:1296` -- `TpyRecord` shape, model for
  `TypeAliasInfo`'s generic metadata.
- `tpyc/codegen_cpp/generator.py:1541` -- `_gen_recursive_union_struct`,
  the templating target for Phase 2.
- `tpyc/sema/cycle_detection.py` -- validator extension target for
  the same-args invariant + transitive-cycle detection.
- `tpyc/sema/type_ops.py:534` -- `substitute_type_params`, the right
  substitution primitive (not the codegen-side homonym).
