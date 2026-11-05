# Literal Types (D7)

## Roadmap

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | `Literal["r", "w"]` string values in `@overload` params | Done |
| 2 | Unified `LiteralType`, int/bool values, `LiteralValue(tag, value)` | Done |
| 3 | Equality narrowing | Done |
| 3a | Dead branch elimination for single-value Literal | Done |
| 3b | Literal overload flattening (per-literal C++ specializations) | Done |
| 3c | Multi-value Literal dead branch elimination | Done |
| 4 | `match`/`case` exhaustiveness for Literal subjects | Done |
| 5 | Literal types in variables (`x: Literal["rb"] = "rb"`) | Done (annotated locals; an unannotated local bound only to literals is checked at a Literal slot by its values); Final variant deferred |
| 6 | General type positions (return types, fields, union flattening) | Return position done (the base's form: `std::string` for a str set); fields done (base-type storage, value-set-checked assignments and defaults); union flattening deferred |

## Future Extensions

| Feature | Notes |
|---------|-------|
| Literal type in `Final` variables | `x: Final = "rb"` infers `Literal["rb"]`. Variant of Phase 5; blocked on three A1 future-extensions (bare `Final`, local `Final`, `Final[Literal[...]]`) all not started -- see FEATURE_ROADMAP A1. |
| TypedDict with Literal keys | `d["name"]` where key is `Literal`. Separate feature (D19) with own design. |
| Cross-function literal propagation | Inferring `Literal` from callers. Not planned -- too complex and fragile. |
| Collision-free name mangling | Current scheme replaces non-alnum with `_`, causing collisions (e.g. `Literal[","]` vs `Literal["_"]`). Switch to hex encoding for non-alnum chars (e.g. `,` -> `x2c`). Low priority -- only matters for unusual literal values. |

---

## Overview

`Literal["r", "w"]` enables compile-time dispatch and narrowing based on
known constant values. `LiteralType(base_type, values)` is the unified
internal representation, with `LiteralValue(tag, value)` distinguishing
`True` from `1`. A `LiteralType` is two facts: its VALUE SET, which
narrows what a slot admits, and its C++ FORM, which is its base type's at
every position (a `Literal[str]` is a `str`: a view parameter, an owned
field, element and return; a local takes the str local rule through the
pending storage entry it carries, `LiteralType.storage`, outside equality).
Every category predicate answers a `Literal` as its base; a reading of the
values asks `literal_values` / `LiteralType.admits` before any of them.

Supported value types: `str`, `int` (including negative), `bool`.
Mixed types in a single `Literal[...]` rejected at parse time.

Resolution: strict pass matches literal values against `Literal` params
(value subset check). Coercion pass falls back to base type matching.
Literal stubs preferred over plain stubs regardless of declaration order.

Admission at a `Literal` slot is one rule. The shared relations
(`resolve_coercion`, `type_matches_numeric`, `types_match_for_inference`,
the pending-number literal store, the container element shortcuts) admit
only a value the set proves (`LiteralType.admits`: a `Literal` within the
set, an int literal of one of its values). Sema turns a proven source into
a `Literal` actual before they run (`literal_admits`,
`tpyc/sema/literal_utils.py`): a literal, or an unannotated local every
binding of which stores a literal and whose address no call takes (the
prescan's `LiteralLocals.literal_only`, whose values are
`LiteralLocals.literals`; `take_ptr(s)` hands a pointer a store could
write it through, so `LiteralLocals.address_taken` leaves it out), so
`s = "r"; if c: s = "w"; f(s)` passes into a `Literal["r", "w"]` parameter
and dispatches to a `Literal` overload, while one out of the set is refused
naming the value (*got 's', which may hold "zz"; pass one of "r", "w"*) or,
in overload ranking, falls back to a base overload. Any other source of the
base type (a `str` parameter, `def get() -> str` into a `Literal[...]` LHS)
is refused: sema cannot prove the runtime value is in the set. Admitted, the
value converts as its base when the bases differ (a BigInt-based copy's
`Literal[2]` into an `int32` `Literal[2, 3]` local renders
`(x).to_fixed_trunc<int32_t>()`).

## Phase 2: Unify Types + Integer/Bool Literals (Done)

Merged `StrLiteralType` and `LiteralStrType` into a unified `LiteralType`.
Extended parser to accept `int`, `bool`, and negative `int` constants in
`Literal[...]`. `LiteralValue(tag, value)` disambiguates `True` from `1`.
All C++ codegen methods delegate to `base_type`.

### What was implemented

**Type system** (`typesys.py`):
- `LiteralValue(tag: str, value: str | int | bool)` -- frozen dataclass,
  `tag` is "str"/"int"/"bool". Prevents Python's `True == 1` ambiguity.
- `LiteralType(base_type: TpyType, values: tuple[LiteralValue, ...])` --
  replaces both `StrLiteralType` and `LiteralStrType`. Delegates `to_cpp()`,
  `to_cpp_param_type()`, `qualified_name()`, etc. to `base_type`.
- `is_any_str_type()` checks `isinstance(t, LiteralType) and t.is_str_base()`.

**Parser** (`parser.py`): `Literal[...]` accepts str, bool (checked before
int since `bool` is `int` subclass), int, and negative int
(`ast.UnaryOp(USub, Constant)`). Mixed types rejected at parse time.
`base_type` is `STR`, `BOOL`, or `INT32` respectively.

**Enrichment** (`calls.py`, now `enriched_source`). Enriches
`TpyStrLiteral` -> str `LiteralType`, `TpyIntLiteral` -> int `LiteralType`,
`TpyBoolLiteral` -> bool `LiteralType`. Passes through already-`LiteralType`
args (enables forwarding Literal-annotated params to other Literal functions).

**Overload matching** (`overloads.py`): Strict pass matches `LiteralType`
vs `LiteralType` (value subset) and `IntLiteralType` vs int-base
`LiteralType`. Coercion pass falls back to base type matching.

**Compatibility** (`compatibility.py`): `LiteralType` compatible with base
type family. `IntLiteralType` compatible with int-base `LiteralType`.

**Codegen**: a `LiteralType` renders as its base's C++ form; codegen has no
Literal-specific path.

### What already works without further phases

**Value restriction**: Literal stubs without a base-type fallback restrict
which values are accepted at compile time:

```python
@overload
def set_priority(level: Literal[1, 2, 3]) -> None: ...
@overload
def set_priority(level: Literal[4, 5]) -> None: ...
# No int32 fallback -- a runtime value is rejected at the call site
def set_priority(level: int32) -> None: ...

set_priority(3)   # ok
n = 3
set_priority(n)   # ok: every binding of `n` is a literal of the set
set_priority(x)   # error: No matching overload (x is a runtime int32)
```


## Phase 3: Equality Narrowing (Done)

Equality-based narrowing for `Literal`-annotated parameters, enabling
type-safe branching within function bodies. Prerequisite for Phase 3b
(Literal overload flattening with different return types).

### What was implemented

**Narrowing** (`narrowing.py`): `_literal_equality_facts()` extracts
narrowing facts from `==`/`!=` comparisons on `LiteralType` variables.
Both forms supported: `mode == "rb"` and `"rb" == mode`. Negative int
literals handled via `TpyUnaryOp("-", TpyIntLiteral)`.

- `==`: true branch narrows to single-value `LiteralType`, false branch
  removes that value (remaining values, or base type if empty).
- `!=`: reversed (false branch gets single value, true branch gets remaining).
- Composes with existing `&&`/`||`/`!` combinators.

**Compatibility** (`compatibility.py`): Added `LiteralType` vs
`LiteralType` subset check -- a narrowed `Literal["rb"]` is compatible
with `Literal["rb", "wb"]` because its values are a subset.

**Overload resolution with narrowed literals**: After narrowing, a
single-value `LiteralType` variable dispatches to the correct overload
stub. The enrichment step (Phase 2) passes through already-`LiteralType`
args without needing AST-level literal detection.

```python
@overload
def classify(mode: Literal["r", "w"]) -> str: ...
@overload
def classify(mode: Literal["rb", "wb"]) -> str: ...
def classify(mode: str) -> str: ...

def dispatch(mode: Literal["r", "w", "rb", "wb"]) -> None:
    if mode == "rb":
        classify(mode)   # mode: Literal["rb"] -> matches binary stub
    elif mode == "r":
        classify(mode)   # mode: Literal["r"] -> matches text stub
```


## Phase 3a: Dead Branch Elimination (Done)

When a variable has been narrowed to a single-value `LiteralType`,
equality checks on it are decided statically.

```python
def f(mode: Literal["r", "rb"]) -> None:
    if mode == "rb":
        if mode == "rb":  # always true: `if (true) { ... }`
            ...
        if mode == "r":   # always false: `if (false) {}`, the arm empty
            ...
```

### What was implemented

The test is decided in sema (`sema/dead_arms.py`, `decided_truth`) from the
name's narrowed `LiteralType`; the arm it rules out is neither analyzed nor
emitted. The statement keeps its test as written: a decided part of it that
only reads becomes its bool literal (`folded_truth`), so the C++ is
`if (true) { ... }` / `if (false) {}`, and a test that runs code stays and
runs for its effects, the live arm after it as a block
(`(void)((ping() && false));`) (`LANGUAGE_FEATURES.md` "Statically decided
branches"). Phase 3c below lists the verdicts.


## Phase 3b: Literal Overload Flattening (Done)

Per-literal function/method variants for `@overload` groups. Every
literal stub gets its own name-mangled C++ function, its decided tests'
dead arms dropped. Enables different return types per literal value.

### What was implemented

**Name mangling** (`typesys.py`): `overload_variant_cpp_name(base, params)`
produces `{name}__lit_{sanitized_values}` for a variant with a `Literal`
parameter. Values sanitized for C++ identifiers. A free function's variant
names are decided once per set by `name_overload_variants` (which also
names the non-literal variants by their parameter types and disambiguates
colliding suffixes, `"a-b"` vs `"a_b"`, by declaration index) and stored as
`FunctionInfo.variant_cpp_name`; see `docs/OVERLOAD_DESIGN.md` "C++ names of
variants". A method's non-literal variants keep the original name.

**The copies** (`sema/method_expansion.py`, `copy_overload_implementation`):
each stub is a `@dispatch` variant whose body is a copy of the
implementation, analyzed at the stub's types; a `Literal` stub's parameter
holds the implementation's type (`literal_member`), so `Literal[2]` over
`x: int` is a BigInt. The copy is analyzed as any body: the test a stub's
literal values decide is decided by the dead-arm rule (`sema/dead_arms.py`)
from the parameter's `LiteralType`, and the arm it rules out is neither
analyzed nor emitted. There is no specialized emitter: each copy is an
ordinary function or method (`docs/OVERLOAD_DESIGN.md`).

**Call-site dispatch** (`thir/lower/checks.py`): a free call spells the
resolved variant's `FunctionInfo.variant_cpp_name`, the same fact the
definition reads; a method call whose `resolved_function_info` has
`LiteralType` params and an overloaded name spells
`overload_variant_cpp_name` (`method_literal_mangled_cpp`). Cross-module
calls use qualified mangled names.

### Example

```python
@overload
def get_field(name: Literal["age"]) -> int32: ...
@overload
def get_field(name: Literal["name"]) -> str: ...
@overload
def get_field(name: str) -> int32 | str: ...
def get_field(name: str) -> int32 | str:
    if name == "age": return 42
    return "hello"
```

Generated C++:
```cpp
int32_t get_field__lit_age(std::string_view name) { return 42; }
std::string get_field__lit_name(std::string_view name) { return "hello"; }
::tpy::Union<int32_t, std::string> get_field__str(std::string_view name) { ... }
```

Call sites: `get_field("age")` -> `get_field__lit_age("age")`,
`get_field(var)` -> `get_field__str(var)`. Every variant of a free overload
set gets its own C++ name (`overload_variant_cpp_name`), so a call always
names the variant sema resolved.


## Phase 3c: Multi-Value Literal Dead Branch Elimination (Done)

Extends dead branch elimination from single-value Literals to multi-value
Literals by analyzing compound conditions: `or`/`and` chains, `in`/`not in`
operators, and `not` wrapping.

### What was implemented

The verdicts live in sema (`sema/dead_arms.py`), one table for every reader:

- `literal_value_from_expr(expr)` (`sema/literal_utils.py`): extracts
  `LiteralValue` from AST nodes.
- `literal_test`: `==` / `!=` / `in` / `not in` (tuple, list or set literal
  right side) over the values a name may hold, with Python equality
  (`Literal[1] == True`, `Literal[0] == False`).
- `literal_chain`: flattens a `&&` / `||` chain of builtin `==` tests (no user
  `__eq__`). For `||`: True if the values compared cover every value of the
  name's `LiteralType`. For `&&`: False if one name must equal two different
  values.
- `decided_truth`: the one decider, combining those with `not` / `and` / `or`.

A test it decides makes its dead arm unanalyzed and empty at every position
(`LANGUAGE_FEATURES.md` "Statically decided branches"). Every maximal decided
part of a test whose evaluated part only reads is replaced by its bool
literal (`folded_truth`), and so is a pure decided compare at a value
position; a ternary or an `and` / `or` value is decided only when its test
folds that way, and is then its live part. An
`@overload` implementation is analyzed once per stub, as a copy at the stub's
parameter types (`docs/OVERLOAD_DESIGN.md`), so a `Literal` stub decides its
copy's tests by the same rule.

### Limitations

- **Partial folding** applies: a decided operand of `and` / `or` that only
  reads gives way to the live one, so `(mode == "r" or mode == "w") and flag`
  over `mode: Literal["r", "w"]` is `if (flag)` (`return flag` at a value
  position).
- **Same variable**: `mode == "r" or other == "w"` cannot do coverage analysis
  across different variables (individual operand folding still applies).
- **`==` only for coverage**: `!=` chains do not participate in set-coverage
  analysis (individual `!=` already folds for out-of-set values).

### Example

```python
@overload
def handle(mode: Literal["r", "w"]) -> str: ...
@overload
def handle(mode: Literal["rb", "wb"]) -> str: ...
def handle(mode: str) -> str:
    if mode == "r" or mode == "w":
        return "text"
    return "binary"
```

Generated variants, each the implementation's body with its decided test:
```cpp
std::string handle__lit_r__w(std::string_view mode) {
    if (true) {
        return "text";
    }
}
std::string handle__lit_rb__wb(std::string_view mode) {
    if (false) {
    }
    return "binary";
}
```

The `||` chain covers all values for `Literal["r", "w"]` (decided true),
and both `==` comparisons are out-of-set for `Literal["rb", "wb"]`
(decided false); the arm each rules out is empty.


## Phase 4: Match/Case on Literal Types (Done)

Match statements exhaustively dispatch on Literal values with narrowing
in each arm, enabling overload dispatch from match bodies.

```python
mode: Literal["r", "w", "rb", "wb"] = ...
match mode:
    case "r" | "w":
        handle_text(mode)    # mode: Literal["r", "w"]
    case "rb" | "wb":
        handle_binary(mode)  # mode: Literal["rb", "wb"]
    # exhaustive -- no default needed
```

### What was implemented

**Sema** (`sema/match.py`): `LiteralType` added to `is_primitive` check,
routing to `_analyze_pattern_nonunion`. `_validate_literal_pattern` unwraps
`LiteralType` to `base_type` for pattern type checking. Exhaustiveness
via `_match_missing_cases` compares `seen_values` against the type's value
set -- missing values produce a warning (consistent with enum/bool/union).

**Narrowing** (`sema/match.py`): `_extract_literal_pattern_values` extracts
matched `LiteralValue`s from patterns (literal, or-pattern, recursive).
Each arm narrows the subject to a `LiteralType` with only the matched
values. Narrowing facts propagate through `type_facts` for codegen.

**Codegen** (`codegen_cpp/match.py`): `LiteralType` subjects route to
existing codegen paths based on `base_type`: string switch/if-elif for
str, primitive switch for int/bool. `_emit_case_body` pushes LiteralType
narrowing facts into `literal_facts` for dead branch elimination within
arm bodies, and snapshots both `literal_facts` and `protocol_narrowings`
so persistent narrowings (e.g. `assert isinstance`) stay case-scoped.
`_SwitchEntry` extended with `type_facts` for the switch path.

**Operator fix** (`sema/operators.py`): `LiteralType` resolved to
`base_type` in `get_effective_type_for_binop` and `_resolve_pending_types`,
enabling operators like `str + Literal[str]` on narrowed variables.


## Phase 5: Literal Types in Variables (Done for annotated locals)

Literal values persist through variable bindings.

**Current state:** `x: Literal["r", "w"] = "r"` retains `LiteralType` on
the local, dispatches to the matching Literal-specialized overload
(`pick__lit_r__w`), and survives branch joins where every assigned value
is in the declared set. Out-of-set assignments (both init-time and
reassignment) are now rejected with a clear `Type mismatch in {context}:
expected Literal[...], got <value>` diagnostic that fires from
`_check_compat` in `sema/compatibility.py`. Works for `Literal[str]`,
`Literal[bool]`, and `Literal[int]` annotated locals.

The **Final variant** -- `x: Final = "rb"` retaining `Literal["rb"]` --
is deferred. It requires three independent A1 future-extension features
(bare `Final`, local `Final`, `Final[Literal[...]]`) that are not yet
started; once those land, the literal-retention layer is a small follow-up.

Bare unannotated locals (`x = "rb"`) keep their base type (`str`, the
default int, `bool`); their values are a per-body prescan fact
(`LiteralLocals.literals`, complete for the `literal_only` locals: every
binding a plain store of an int, bool or str literal -- no augmented store,
no closure `nonlocal` rebind, no other binding form; a call taking the name
does not disqualify it). A `Literal` slot admits such a local when every
value is in its set (`literal_utils.literal_admits`), and an overload
`Literal` stub matches it the same way (`enriched_source`); an out-of-set
value or any other local is refused at a `Literal` parameter, naming the
value (*'s', which may hold "zz"; pass one of "r", "w"*), and in overload
matching is a non-match, so the call takes the `str` / base stub. A local
copied from a `Literal` (`y = x`) keeps the `Literal` type
(BUGS.md#literal-local-copy-keeps-set).

### Problem (historical)

Historically, `x = "rb"` produces `StrType` (or `PendingStrType` for locals).
The literal value is lost. To enable `open(file, x)` dispatching to the
binary overload, the variable must carry the literal value in its type.

Note: Phase 3 already makes `Literal`-annotated parameters useful, and the
annotated-local path (`x: Literal["rb"] = "rb"`) ended up working too. The
practical gap that remains is when a literal is assigned to a local without
annotation and then passed to a Literal-dispatched overload.

### Design Options

**Option A: Always produce literal types for string/int/bool literals.**

`analyze_expr(TpyStrLiteral("rb"))` returns `LiteralType(StrType, ("rb",))`
instead of `StrType`. Same for int/bool literals.

Pros: Simple. Would also unblock bare-int dispatch to `Literal[int]` overloads
(today `x = 1; pick(x)` dispatches to the plain `int` overload, not the
`Literal[1, 2]` specialization -- the `IntLiteralType` defined in `typesys.py`
exists for int-literal constant folding, but does NOT propagate to overload
resolution on unannotated locals).
Cons: Every `isinstance(t, StrType)` check (45+) needs to also handle
`LiteralType`. Massive ripple.

**Option B: Produce literal types only for `Final` and `Literal`-annotated
variables.**

`x = "rb"` stays `StrType`. But:
- `x: Final = "rb"` produces `LiteralType(StrType, ("rb",))`
- `x: Literal["rb"] = "rb"` produces `LiteralType(StrType, ("rb",))`

Pros: Minimal ripple (annotated variables only). Opt-in.
Cons: The common case `mode = "rb"; open(f, mode)` still doesn't work
unless the user adds an annotation. However, `mode: Literal["rb"] = "rb"`
is not much extra typing, and Phase 3 makes annotated params work well.

**Option C: Track literal values in `PendingStrType`, resolve to
`LiteralType` when provably single-valued.**

During local variable inference, track the literal source. If all
assignments to a variable are the same literal value, resolve to
`LiteralType`. Otherwise resolve to `StrType`/`StrViewType` as today.

Pros: Works automatically for common patterns. No annotation needed.
Cons: More complex inference. Doesn't work across function boundaries.
Reassignment (`mode = "rb"; if ...: mode = "wb"`) would lose the literal.
Requires extending `ViewVarInfo` in `local_deduction.py` and adding a
`literal_string_values` tracking dict parallel to the existing
`literal_values` dict for integers.

**Decision deferred** -- Phase 3 (narrowing) is useful without this.
Revisit after seeing real usage patterns with `Literal`-annotated params.

### Effort: M

Main work: audit and update 45+ isinstance checks (if Option A). The
`IntLiteralType` pattern provides a template. The deduction/reassignment
logic exists in `local_deduction.py` (already handles `literal_values`
dict for integers).


## Phase 6: Literal as General Type

### Return Types (Done)

```python
def get_mode() -> Literal["r", "rb"]: ...
mode = get_mode()  # mode: Literal["r", "rb"]
```

`LiteralType.to_cpp_return()` (and `to_cpp_return_const()`) delegates to
the base, so a `Literal[str]` returns `std::string`, exactly as `-> str`
does, and a caller's local bound to the result owns it. A static
`std::string_view` form for every `Literal[str]` position is a filed
alternative (TODO.md "Reconsider a static `std::string_view` form for
`Literal[str]`"); it needs every source of a `Literal[str]` value checked
and canonicalized to the static literal first.

### Fields (Done)

```python
class Config:
    mode: Literal["debug", "release"] = "debug"
```

Stored as the base type in C++. The constraint is compile-time only:
field assignments and defaults are checked against the value set. A
constructor or dataclass taking a `Literal` parameter cannot be called
yet (`BUGS.md#literal-param-proxy-gates-reject-plain-call`).

### Union Interaction

```python
Literal["a"] | Literal["b"]  ==  Literal["a", "b"]
```

Flattening rule: when unioning `LiteralType` with same base type, merge
value sets. Requires extending `make_union()` in `typesys.py` to detect
and merge `LiteralType` members.

### Effort: L

Touches type compatibility, record codegen, union flattening (`make_union`),
codegen return type unwrapping, and potentially type inference in many
contexts.


## Open Questions

1. **Interaction with `PendingStrType`**: Resolved. A `Literal[str]` is a
   `str` in C++ at every position: a parameter is a `std::string_view`, a
   return and a field are `std::string`, and a `Literal[str]` local's
   storage (view or owned) is decided by the same `PendingStrType` /
   `ViewVarInfo` rule as its `str` twin, so an alias of an owned source or
   an owning sink promotes it as it does a `str` local.

2. **Cross-function literal propagation**: `def f(mode: Literal["r", "w"])`
   is straightforward (annotation in signature). But inferring literal types
   from callers (`f("r")` implies `mode` could be `Literal["r"]`) is not
   planned -- too complex and fragile.

3. **TypedDict integration**: `Literal` keys in TypedDict
   (`d["name"]` where key is `Literal`) is a separate feature (D19 in
   roadmap) that depends on Literal types but has its own design concerns.

4. **Forwarding `LiteralType` args through calls**: Resolved in Phase 2.
   `enriched_source` (`calls.py`) passes through already-`LiteralType`
   args without requiring AST-level literal detection.

5. **Name mangling collisions in flattening**: Resolved for free
   functions. The suffix is not injective (`"42"` and `42`, `"a-b"` and
   `"a_b"` give one suffix), so `name_overload_variants` gives a variant
   whose readable name collides -- inside its set or with a name the
   module binds -- its declaration index (`pick__lit_a_b__v0`). Method
   variants are named the same way since 2026-10-27, reserved against the
   record's whole member namespace.
