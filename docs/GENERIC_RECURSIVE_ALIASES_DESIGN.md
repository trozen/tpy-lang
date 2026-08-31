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
a clean diagnostic; Phase 2 lands the templated wrapper struct.

Design revision 2 (incorporates Codex review + independent review) restructured
around the principle that **only recursive aliases have C++-level nominal
identity** (a generated wrapper struct); non-recursive generic aliases are pure
sema-only expansion.

**Design revision 3 (2026-05, Phase 2 refresh against master).** The original
Phase 2 plan predated master's recursive-union representation (`AliasRef`
placeholder + member-tuple-keyed `union_wrapper_index` + `needs_wrapper()`).
Refreshed against that code, with a second Codex review pass, the use-site
carrier changed from "plain `NominalType`" to a **dedicated
`RecursiveAliasInstanceType`** that carries `alias_info` on the type itself
(no global name-keyed lookup -- avoids a same-short-name cross-module
collision) and is explicitly union-like (so plain `NominalType` consumers --
records, enums, protocols -- stay untouched). Consumers route through
capability helpers (`is_recursive_union_like` / `recursive_union_alternatives`),
scoped to the wrapper/recursive-union conversion
sites a recursive-alias value actually flows through. See the refreshed
"Phase 2" sections below.

**Phase 2 shipped (2026-05, revision 4 -- synced to as-built).** Generic
recursive aliases now compile and run end-to-end. Revision 4 reconciles this
doc with the implementation, which diverged from the rev-3 plan in two ways
discovered during the build (both reviewed):

- **Option D (uniform `AliasRef` + a single sema finalize pass).** The parser
  and semantic analyzer use *separate* `TypeRegistry` objects, and use-site
  type resolution is a parse-phase activity -- so a use site cannot mint a
  `RecursiveAliasInstanceType` (it lacks the sema-registry `alias_info`).
  Instead, parse-resolution emits an `AliasRef(name, args)` for *both* the
  in-body self-reference *and* every use site, and one early-sema pass
  (`_finalize_generic_recursive_aliases`) converts them to instances against
  the sema registry. This supersedes the rev-3 "use site returns the instance
  directly + finalize rewrites only bodies" sketch in the sections below.
- **`(qname, type_args)` identity.** `TypeAliasInfo` is not hashable (it has
  `list` fields), so it cannot be a frozen-dataclass eq/hash field. The
  instance keys identity on `(qname, type_args)` and carries `alias_info`
  (and the local render name) as `compare=False` payload. The rev-3 dataclass
  sketch below (`(alias_info, type_args)`) is corrected in the Carrier section.

The remaining v1 gaps and follow-ups discovered during the build are collected
under "As-built gaps (revision 4)" near the end of this doc.

## Goals (v1)

- Generic non-recursive aliases: `type Pair[T] = tuple[T, T]`.
- Generic recursive aliases: `type Tree[T] = T | list[Tree[T]]`.
- Multi-parameter aliases: `type Pair[K, V] = tuple[K, V] | ...`.
- Cross-module: alias defined in module A, used in module B.
- `isinstance(x, AliasName)` (bare name) works on the **recursive** form
  only. Non-recursive aliases reject `isinstance` -- they have no
  runtime identity. **(As-built: deferred.** `isinstance(x, Tree)` on a
  recursive generic alias is still rejected with the generic-alias "no
  runtime identity" diagnostic -- the fold-to-True path was not built. It is
  a trivially-true check and low value; tracked in follow-ups.)
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

| Aspect | Non-recursive generic alias | Recursive generic alias (as-built) |
|---|---|---|
| Sema use-site resolution | Substitute body; return expanded type | Parse emits `AliasRef`; sema finalize -> `RecursiveAliasInstanceType` |
| C++ identity | None -- alias does not exist in C++ | `template<...> struct Tree { variant<...> value; };` |
| `isinstance(x, Name)` | **Rejected** at sema (no runtime identity) | **Rejected** too (fold-to-True deferred -- see As-built gaps) |
| Member expansion | Eager at sema use-site | On-demand via `recursive_union_alternatives` (narrowing / match) |
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
`tpyc/cycle_detection.py` walks the union body checking
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

   **As-built:** the distinct diagnostic fires for cycles that *resolve*
   far enough to be tagged by `_detect_recursive_unions` -- in practice the
   generic-alias <-> generic-*record* cycle (`type Expr[T] = Lit[T] | Op[T]`
   with `Op[T]` holding `Box[Expr[T]]`), where the records register before
   aliases. A pure alias <-> alias cycle (`Forest`/`Tree`) is *also* rejected,
   but earlier and with a coarser message: neither alias can be defined first
   without the other being an "Unknown generic type" forward reference, so it
   dies at parse-resolution before the validator runs. Both are rejected;
   only the message quality differs for the alias-alias path.

3. **Conflicting recursive args** (`type Bad[T] = T | list[Bad[int]]`,
   `type Pair[K, V] = ... | dict[V, Pair[V, K]]`): caught by check (1)
   -- the recursive position's args aren't bare `TypeParamRef`s
   matching positionally. Diagnostic: `"recursive position of alias
   'Bad' must reuse type parameter 'T' at position 0; got 'int'. v1
   supports identity recursion only."`

4. **Mark the alias**: on success, set `TypeAliasInfo.is_recursive =
   True`. This drives Phase 2's branch in sema use-site resolution
   (see below).

### Carrier: `RecursiveAliasInstanceType` (revision 3)

The resolved semantic type for a generic recursive alias use site
(`Tree[Int32]`) is a **dedicated frozen type**, not a plain
`NominalType`. As-built shape (`tpyc/typesys.py`):

```python
@dataclass(frozen=True)
class RecursiveAliasInstanceType(TpyType):
    qname: str                         # defining module + short name; IDENTITY
    type_args: tuple[TpyType, ...]
    name: str = field(compare=False, hash=False)   # local render name
    alias_info: TypeAliasInfo = field(compare=False, hash=False)  # semantics
```

Identity is `(qname, type_args)` -- `TypeAliasInfo` is not hashable (it has
`list` fields), so it cannot be an eq/hash field; it rides along
`compare=False` as the semantic payload. `name` is the *local* reference name
(e.g. the importing-side name), used only for C++ rendering via
`native_cpp_names` -- mirroring how cross-module records render -- so two
same-short-named aliases imported into one module do not collide on
rendering.

Why a dedicated type rather than overloading `NominalType` (the original
revision's choice):

- **No global name-keyed dict.** Alias identity is the qname and the body
  rides on `alias_info`, so `alternatives()` / `is_value_type()` /
  `wrapper_info()` need no `get_current_compiler()` lookup. A name-keyed
  `dict[str, TypeAliasInfo]` would mis-resolve two modules that both
  define `type Tree[...]` -- the qname identity is collision-proof.
- **`NominalType` stays clean.** It is shared by records, enums, and
  protocols; teaching every `NominalType` consumer that "some nominals
  are secretly recursive unions" is a smell. A first-class union-like
  type is honest and THIR-friendly.

Semantics (all derived from on-type `alias_info`, no compiler context):
- `recursive_union_alternatives()`: substitute `{type_params[i]:
  type_args[i]}` into `alias_info.body`'s union members.
- `is_value_type()`: delegate to the substituted union's semantics
  (mirrors the non-generic `UnionType` wrapper -- does *not* just return
  True because the wrapper struct is copyable).
- `to_cpp()`: render `Tree<int32_t>` via the alias qname + the
  per-compilation `native_cpp_names` map (the one legitimate
  compiler-context touch, identical to existing `AliasRef`/`UnionType`/
  `NominalType` name mangling).

### Use-site resolution + finalize (revision 4 -- Option D, as-built)

Rev 3 planned for the use site to mint the instance directly. That is not
possible here: the parser and sema use *separate* `TypeRegistry` objects
(`Parser` constructs its own; `SemanticAnalyzer` builds a fresh one and
re-registers each alias from a cross-phase tuple), and use-site type
resolution runs in the **parse** phase -- which cannot see the sema-registry
`alias_info`. So the as-built flow is **Option D**:

1. **Parse-resolution emits `AliasRef(name, args)`** for both the in-body
   self-reference (`type_resolver._resolve_ref`, the `_pending_alias` branch)
   *and* every use site (`_resolve_generic_alias_use`, the `is_recursive`
   branch). Arity is checked here; args are resolved (restoring the
   "Unknown type" diagnostic for malformed args). `AliasRef` is a pure
   forward placeholder -- never the semantic carrier for a value.
2. **One early-sema pass `_finalize_generic_recursive_aliases`**
   (`tpyc/sema/analyzer.py`, run from the records&protocols phase, before
   body analysis) rewrites every generic-recursive `AliasRef` into
   `RecursiveAliasInstanceType` against the **sema** registry's `alias_info`.
   It walks: the alias's own stored body (the registered `TypeAliasInfo.body`
   *and* the cross-phase tuple), function/method signatures, record fields,
   globals, **and function-body local annotations** (which `resolve_refs`
   also resolves at parse time, so they carry the same placeholders). One
   conversion point, one `alias_info` source -- no cross-registry divergence.
   The pass runs whenever the module defines *or imports* a generic recursive
   alias. `_alias_lookup_for_finalize` resolves the alias_info + qname for
   both local and imported aliases (imported-first, since `from m import Tree`
   also registers a local entry whose module name would otherwise mis-stamp
   the qname).

Downstream, alternatives are expanded on demand via
`recursive_union_alternatives(typ)` (match dispatch, narrowing) and rendering
emits `Tree<int32_t>`; the wrapper template handles the rest. This mirrors how
generic records resolve at use sites while keeping union-like behavior off
`NominalType`.

### Central capability helpers (revision 3)

Consumers ask about wrapper-ness by **capability, not by file list**:

- `is_recursive_union_like(t)` -- True for the non-generic `UnionType`
  wrapper *and* `RecursiveAliasInstanceType`.
- `recursive_union_alternatives(t)` -- the variant alternatives behind
  the wrapper (`UnionType.members` / substituted instance alternatives).

(As-built: a third helper `recursive_union_info` was sketched but dropped --
match/codegen read wrapper identity via `wrapper_info()` directly on the type,
so it had no callers.)

Apply these at the conversion sites a recursive-alias value flows
through: alternative -> wrapper construction, wrapper -> `.value`
variant access, param/return lowering, assignment compatibility,
isinstance/narrowing, match lowering, equality/printing. **Do not**
sweep every `isinstance(t, UnionType)` branch -- plain structural
`A | B` value-variants never become a recursive-alias instance.

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
6. Forward declarations in `generator.py`'s forward-decl pass add `template<...>
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

The `case _` arm is the catch-all for the T alternative until
type-param-as-pattern is a separate feature. Tests use these
patterns, not `case T()`.

**As-built notes:**

- The rev-3 alternative (`if isinstance(t, list): ... else: leaf: T = t`)
  does **not** work: bare `isinstance(x, list)` is unsupported even for a
  plain union (pre-existing -- the second arg `list` has no element type to
  resolve), and `case _` does not narrow the subject to the remaining
  alternative either. Use `match` with `case list()` / `case dict()` for the
  container arm and `case _` for the leaf.
- **Leaf-value extraction needs a concrete leaf type.** A fully generic
  `[T]` traversal can only use `case _` for the leaf (the `T` value is not
  bound -- count-style traversal). To read the leaf value, the leaf type must
  be concrete so a class pattern matches it: e.g. `sum_leaves(t: Tree[int])`
  uses `case int() as v: return v`.
- The match subject stays the `RecursiveAliasInstanceType` for codegen (its
  `wrapper_info()` drives `.value` variant dispatch); sema analyzes the arms
  against a synthesized `UnionType` of the instance's alternatives so the
  existing union arm-analysis applies unchanged.

### Registry adaptation (revision 3)

`recursive_union_names: set[str]` stays name-keyed. The wrapper is one
template per alias name, so name-keyed membership is correct.

The member-tuple-keyed `union_wrapper_index` stays **non-generic-only**.
Generic recursive aliases never register there -- `_register_union_wrappers`
and `_fix_recursive_optional_annotations` skip aliases with `type_params`
(this is carry-over #2's deliberate resolution: it prevents
`TypeParamRef`-keyed entries from leaking into the concrete-union index).
Generic-alias recognition uses the on-type `alias_info` carried by
`RecursiveAliasInstanceType`, not a global lookup -- which is why
revision 3 has **no** `generic_recursive_aliases` compiler dict (an
earlier sketch proposed one; it was dropped as a same-short-name
collision hazard).

`is_recursive_union` reverse-map (frozenset-of-members -> name) in
`sema/context.py`: leave non-generic-only -- generic instances carry
their identity on the type, so they never query the reverse-map.

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
qualified.

**As-built:** cross-module works through **all three import forms** --
`from m import Tree`, aliased `from m import Tree as T2`, and qualified
`import m; m.Tree[...]`.

- The use-site `AliasRef` carries the *local* short spelling as its render name
  (so an aliased import resolves under `T2`, the key its render name is
  registered under) and the *defining* module on `AliasRef.module` for a
  qualified reference.
- `_alias_lookup_for_finalize` resolves a qualified reference against the
  defining module's alias table **first** (by `AliasRef.module`), so a local /
  imported alias of the same short name never shadows it. Bare references
  (local, `from`-import, aliased) fall through to the imported-first / local
  path.
- The finalize *run-trigger* also fires when an imported user module defines a
  generic recursive alias (the qualified `import m` form leaves no
  `imported_type_alias_info` entry, so the old trigger would skip the pass).
- Rendering is keyed by **canonical qname**, not the short name:
  `Compiler.recursive_alias_cpp_names` maps `defining_module.original_name` to
  the qualified C++ wrapper, populated per codegen-module pass for *imported*
  aliases (from both `imported_type_alias_info` and qualified module imports)
  and reset by `clear_codegen_state` at the head of each pass.
  `RecursiveAliasInstanceType.to_cpp` looks up by `self.qname`; a same-module
  alias is absent and renders bare -- so the reset is load-bearing, not
  cosmetic: an importer's leftover entry would qualify the definer against
  itself on any later pass over it. This is collision-proof: a module with a
  local `Tree[T]` and a qualified `other.Tree[int]` emits a bare `Tree<...>`
  and `::tpyapp::other::Tree<...>` respectively (the qualified form is the only
  one that lets two same-short-named wrappers coexist in one module, which the
  prior short-name `native_cpp_names` channel could not represent). (Note:
  `TypeAliasInfo` did not gain a `defining_module` field as rev-3 sketched.)

### Tests (Phase 2 -- as-built)

Shipped under `tests/cases/union/` (error cases use the
`error_generic_alias_*` prefix, not the rev-3 `error_recursive_*` names):

Happy path:
- `generic_recursive_tree_int`: `type Tree[T] = T | list[Tree[T]]` --
  construct nested, generic `leaf_count[T]` (`case _` leaf), concrete
  `sum_leaves` (`case int()` binds the leaf value), equality, printing.
- `generic_recursive_tree_str`: same alias instantiated with `str`
  (multi-instantiation dispatch).
- `generic_recursive_two_param`: `type DictTree[K, V] = V | dict[K,
  DictTree[K, V]]` (multi-param, dict-alternative dispatch).
- `generic_recursive_box_field`: `class Holder[T]: data: Box[Tree[T]]`
  (`no_cpython` -- Box storage + match diverges under the CPython stub).
- `generic_recursive_cross_module`: alias + traversal in `treelib`, used
  from `main` via `from treelib import Tree`.

Error cases:
- `error_generic_alias_recursive_arity`: `list[Tree[T, T]]` -- self-ref
  arity error (parse-resolution).
- `error_generic_alias_recursive_non_identity`: `list[Weird[list[T]]]`
  -- "must reuse type parameter" (growing-args).
- `error_generic_alias_recursive_swap`: `dict[V, Pair[V, K]]` --
  "must reuse type parameter" (swapped-args).
- `error_generic_alias_recursive_concrete`: `list[Bad[int]]` --
  "must reuse type parameter" (constant-args).
- `error_generic_alias_mutual_recursion`: generic alias <-> generic
  record cycle (`Expr` / `Lit` / `Op` with `Box[Expr[T]]`) -- "mutual
  recursion across generic aliases".
- `error_generic_alias_recursive_generic_nonunion`: `type Bag[T] =
  list[Bag[T]]` -- non-union recursive alias rejected ("must use a union
  form").

**Dropped / deferred from the rev-3 list:**
- `generic_recursive_tree_nested` (`Tree[list[Int32]]`) -- *both*
  alternatives are list-shaped (`list[Int32]` leaf, `list[Tree[...]]`
  branch), so construction/dispatch is inherently ambiguous; needs a
  multi-list-alternative disambiguation design.
- `generic_recursive_non_eq_t` -- the constrained `operator==` is emitted,
  but a "C++ should fail to compile" assertion doesn't fit the snapshot
  harness (it expects a successful build/run).
- Codex-suggested `Tree[Tree[int]]` and passing/returning-alternatives /
  reassignment cases remain unwritten (`Tree[Tree[int]]` hits the
  dropped/ambiguous nested gap above). The same-short-name collision is now
  covered (`generic_recursive_alias_name_collision`), landed with the
  qualified/aliased cross-module use-site work.

All existing non-generic recursive alias tests keep passing unchanged.

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

### Phase 2 commits (as-built)

7. **`AliasRef.args` + identity-recursion validator** -- extend `AliasRef`
   with `args` (eq/hash + `inner_types`/`with_inner_types` so
   `substitute_type_params_structural` recurses; `to_cpp` renders `Tree<T>`).
   Self-ref site resolves args + arity-checks (restores the Unknown-type
   diagnostic). Extend `validate_recursive_union_paths`
   (`tpyc/cycle_detection.py`) with the identity-recursion check, guarded to
   `type_params` aliases (non-generic unaffected). The transitive-cycle
   rejection moved to commit 8 (coupled to gate removal). `is_recursive` is
   set at parse-resolution.
8. **`RecursiveAliasInstanceType` + finalize pass + use-site resolution** --
   introduce the dedicated type (`(qname, type_args)` identity, `alias_info`
   carried `compare=False`, delegated `is_value_type`, per-member
   `alternatives()`, `to_cpp` via the local render name). Use-site resolution
   returns an `AliasRef` (Option D -- see "Use-site resolution + finalize"),
   and the single sema finalize pass converts bodies + all annotation use
   sites. Add the capability helpers. Skip generic aliases in
   `_register_union_wrappers` / `_fix_recursive_optional_annotations`. Remove
   both rejection gates. Add the transitive-cycle rejection (a generic alias
   tagged recursive via the cycle detector rather than a direct self-ref).
9. **Wrapper struct templating** -- `_gen_recursive_union_struct` emits
   `template<...>` via `gen_record_template_header`, sentinel ctor template
   param (`_TpyAliasCtorArg`), constrained `operator==`, constrained default
   ctor, header-only; templated forward decls. Non-generic path byte-identical.
10. **Route consumers through the helpers** -- assignment/return compat
    (`sema/compatibility.py`), list/dict-literal construction + array-literal
    codegen (`sema/expressions.py`, `codegen_cpp/expressions.py`), generic
    inference (`sema/type_ops.py`), match dispatch (`sema/match.py` +
    `codegen_cpp/match.py`). `_variant_index` / `VariantAccess` already read
    via `wrapper_info()` / `needs_wrapper()`, which the instance implements.
11. **Tests + docs** -- ship the happy-path suite (see "Tests (Phase 2 --
    as-built)"); flip `docs/LANGUAGE_FEATURES.md` to Working; file the
    pre-existing/adjacent bugs (`.template`-keyword, generic-class ctor
    inference through `Box[Tree[T]]`) in `BUGS.md`. Folded in three
    consumer-routing fixes surfaced by the cross-module + two-param tests
    (dict-literal value coercion, imported-alias finalize + qualified C++
    name registration, local-name rendering).

Each step was one commit on the `generic-type-aliases` branch (plus a
preceding doc-refresh commit for revision 3).

---

## Risks

- **Sentinel ctor template param**: `_TpyAliasCtorArg` chosen to be
  ugly and obviously compiler-emitted. If a user-supplied bound or
  alias name ever shadows it (unlikely), error message points at the
  sentinel name and we add an explicit reservation. Mitigation: a
  parse-time check that no user type-param starts with `_TpyAlias`.
  **(As-built: the mitigation check was not added -- left as a latent
  follow-up; collision is extremely unlikely.)**
- **Constrained auto-default machinery**: `operator==` and default
  ctor now have `requires` clauses. C++ overload resolution behavior
  when these constraints aren't satisfied differs subtly from "method
  doesn't exist" vs "method exists but unusable." **(As-built: the
  `non_eq_t` test that would have pinned the error-site quality was
  dropped -- a "C++ should fail to compile" assertion doesn't fit the
  snapshot harness. The constraints are emitted; the error-site quality
  is unverified by an automated test.)**
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

## As-built gaps (revision 4)

What shipped vs. the v1 plan, for the next person:

- **`isinstance(x, Tree)` on the recursive form** (Goal, line ~48): not
  built -- still rejected with the generic-alias "no runtime identity"
  diagnostic. Low value (trivially true).
- **`tree_nested` (`Tree[list[Int32]]`)**: not supported -- both
  alternatives are list-shaped, so literal construction / `case list()`
  dispatch is ambiguous. Needs a multi-container-alternative disambiguation
  design (annotate which alternative, or forbid).
- **Same-module protocol method typed with the alias**: fails the C++ build
  -- a protocol (structural or `@dynamic`) defined in the *same* module as the
  alias emits its concept / base / `Adapter`/`RefAdapter` before the wrapper
  struct, so the method signature references an undeclared `Tree<...>`. Works
  when the alias is imported (the wrapper arrives complete via the header).
  Filed in `BUGS.md`; covered cross-module by
  `tests/cases/protocols/dyn_recursive_alias_method`. Needs the codegen
  emit-ordering fix (the local wrapper fwd-decl must precede protocol
  emission, as the imported-header path already guarantees).
- **Qualified / aliased cross-module use sites** (`m.Tree[int]`,
  `from m import Tree as T2`): **DONE** (branch `generic-alias-xmodule-use`).
  All three import forms resolve; rendering moved from the short-name
  `native_cpp_names` channel to a qname-keyed render map so a local and a
  qualified same-short-named alias stay distinct. See the cross-module section
  above for the as-built mechanism. Tests: `generic_recursive_cross_module_qualified`,
  `_aliased`, `generic_recursive_alias_name_collision`.
- **Alias <-> alias mutual-recursion diagnostic**: rejected via a coarse
  `Unknown generic type` forward-ref error rather than the distinct "mutual
  recursion across generic aliases" message (which the alias <-> record cycle
  does get).
- **Generic narrowing to the leaf**: a fully generic `[T]` traversal can't
  bind the `T` leaf value (`case _` only); leaf extraction needs a concrete
  leaf type + class pattern (`case int()`). Bare `isinstance(x, list)` is
  unsupported (pre-existing). **A UNION leaf is narrower still: no member of
  it is nameable.** `Tree[Int32 | str]` rejects `case Int32():` and
  `case str():` alike, because the wrapper's variant carries one slot for the
  whole leaf and a member has no slot of its own to dispatch on. Sema owns
  that rejection (it reads the same member list codegen does); the residual
  wants nested-variant dispatch. Filed in `BUGS.md`.
- **`None` as a leaf, by spelling**: `Tree[None]` compiles and matches
  (`tests/cases/match/union_recursive_none_leaf`), but the direct union
  `type Nest = None | list[Nest]` is rejected at parse time -- the parser
  canonicalizes a `None`-bearing union into an optional and then sees no
  union to recurse through. Same canonicalization class one layer earlier,
  so the two want fixing together. Filed in `BUGS.md`.
- **Filed in `BUGS.md` (adjacent, surfaced here):** member-template call on a
  dependent receiver inside a generic function omits `.template`; generic-class
  ctor inference doesn't deduce `T` through a nested `Box[Tree[T]]` arg
  (annotate explicitly).
- **Return convention (Family C, landed on `recursive-union-ref-return`):** the
  wrapper now follows the reference-type return convention -- a bare `-> Tree[T]`
  return lowers to `Tree<T>&`, a fresh value requires `Own[Tree[T]]` (rejected
  bare), and a field/param accessor returns the reference. **Locals
  & match subjects (Part A, landed):** wrapper *locals* and `match`-on-call
  subjects now bind *by reference* when the source is a reference (`v = h.view()`
  -> `Tree<T>&`, `match h.get()` -> `auto&`), so reads no longer copy the variant
  tree and caller mutations through them reach the field (CPython aliasing); a
  fresh value still binds by value. **Params (Part B, landed):** wrapper params
  are routed through normal const-inference (read-only -> `const Tree<T>&`,
  returned-by-reference or mutated -> `Tree<T>&`), matching record params, so
  `def f(e: Tree[T]) -> Tree[T]: return e` works without a `readonly[]` /
  `Own[]` workaround. The `@auto_readonly` usage-dependent receiver const-ness
  prerequisite (a read-only-through-`Box.get` param stays const) landed first on
  master.

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

## References (as-built -- symbols, not line numbers, which churn)

- `docs/FEATURE_ROADMAP.md` -- D19 "Recursive Type Aliases" Done; this
  work is the listed D19 extension.
- `docs/LANGUAGE_FEATURES.md` -- generic recursive aliases marked Working.
- `tpyc/typesys.py` -- `RecursiveAliasInstanceType` (+ `AliasRef.args`,
  capability helpers `is_recursive_union_like` / `recursive_union_alternatives`).
- `tpyc/sema/analyzer.py` -- `_finalize_generic_recursive_aliases` (Option D
  finalize pass), `_alias_lookup_for_finalize`, transitive-cycle rejection in
  `_detect_recursive_unions`, gate removal in the alias-registration loop.
- `tpyc/parse/type_resolver.py` -- self-ref + use-site `AliasRef` emission
  (`_resolve_generic_alias_use`).
- `tpyc/cycle_detection.py` -- `validate_recursive_union_paths`
  identity-recursion check.
- `tpyc/codegen_cpp/generator.py` -- `_gen_recursive_union_struct` templated
  branch + imported-alias qualified-name registration.
- `tpyc/sema/{compatibility,expressions,type_ops,match}.py`,
  `tpyc/codegen_cpp/{expressions,match}.py` -- consumer routing (commit 10/11).
- Non-generic sibling (cross-module coercion): `TypeRegistry.resolve_alias_ref`
  is the single module-aware resolver for a non-generic recursive-union
  `AliasRef` (defining-module-first, like `_alias_lookup_for_finalize`), so
  `json.dumps([1, 2, 3])` / `[1, None, 3]` work without importing the
  `JsonValue` alias. Every site that resolves such an `AliasRef` -- coercion
  (`compatibility`), codegen elem-target + None-monostate rendering
  (`expressions`), match dispatch, narrowing -- routes through it. C++ wrapper
  qualification is registered in `generator.py`'s imported-recursive-union loop
  (short-name -> qualified).
