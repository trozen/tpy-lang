# Literal Types (D7)

## Roadmap

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | `Literal["r", "w"]` string values in `@overload` params | Done |
| 2 | Unified `LiteralType`, int/bool values, `LiteralValue(tag, value)` | Done |
| 3 | Equality narrowing | Done |
| 3a | Dead branch elimination for single-value Literal | Done |
| 3b | Literal overload flattening (per-literal C++ specializations) | Done |
| 4 | `match`/`case` exhaustiveness for Literal subjects | Done |
| 5 | Literal types in variables (`x: Literal["rb"] = "rb"`) | Not started |
| 6 | General type positions (return types, fields, union flattening) | Not started |

## Future Extensions

| Feature | Notes |
|---------|-------|
| Literal type in `Final` variables | `x: Final = "rb"` infers `Literal["rb"]`. Variant of Phase 5. |
| TypedDict with Literal keys | `d["name"]` where key is `Literal`. Separate feature (D19) with own design. |
| Cross-function literal propagation | Inferring `Literal` from callers. Not planned -- too complex and fragile. |
| Collision-free name mangling | Current scheme replaces non-alnum with `_`, causing collisions (e.g. `Literal[","]` vs `Literal["_"]`). Switch to hex encoding for non-alnum chars (e.g. `,` -> `x2c`). Low priority -- only matters for unusual literal values. |
| Multi-value Literal dead branch elimination | `if mode == "r" or mode == "w":` where mode is `Literal["r", "w"]` should fold to `true`. Currently only single-value Literals fold individual `==` comparisons. Requires analyzing `or`/`and`/ternary chains to detect full-set coverage. The C++ compiler cannot optimize this since the C++ signature is just the base type. |

---

## Overview

`Literal["r", "w"]` enables compile-time dispatch and narrowing based on
known constant values. `LiteralType(base_type, values)` is the unified
internal representation, with `LiteralValue(tag, value)` distinguishing
`True` from `1`. Delegates all C++ codegen to `base_type`.

Supported value types: `str`, `int` (including negative), `bool`.
Mixed types in a single `Literal[...]` rejected at parse time.

Resolution: strict pass matches literal values against `Literal` params
(value subset check). Coercion pass falls back to base type matching.
Literal stubs preferred over plain stubs regardless of declaration order.

Current limitation: only direct literal arguments dispatch. Variables
fall through to the base type fallback.

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

**Enrichment** (`calls.py`): Renamed to `_enrich_literal_types`. Enriches
`TpyStrLiteral` -> str `LiteralType`, `TpyIntLiteral` -> int `LiteralType`,
`TpyBoolLiteral` -> bool `LiteralType`. Passes through already-`LiteralType`
args (enables forwarding Literal-annotated params to other Literal functions).

**Overload matching** (`overloads.py`): Strict pass matches `LiteralType`
vs `LiteralType` (value subset) and `IntLiteralType` vs int-base
`LiteralType`. Coercion pass falls back to base type matching.

**Compatibility** (`compatibility.py`): `LiteralType` compatible with base
type family. `IntLiteralType` compatible with int-base `LiteralType`.

**Codegen** (`functions.py`, `expressions.py`): `_overload_stubs_are_literal_only`
and `_is_str_view_at_runtime` updated to use `LiteralType`.

### What already works without further phases

**Value restriction**: Literal stubs without a base-type fallback restrict
which values are accepted at compile time:

```python
@overload
def set_priority(level: Literal[1, 2, 3]) -> None: ...
@overload
def set_priority(level: Literal[4, 5]) -> None: ...
# No Int32 fallback -- variable args rejected at call site
def set_priority(level: Int32) -> None: ...

set_priority(3)   # ok
set_priority(x)   # error: No matching @overload
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
equality checks on it fold to `true`/`false` in the generated C++.

```python
def f(mode: Literal["r", "rb"]) -> None:
    if mode == "rb":
        if mode == "rb":  # -> if (true)
            ...
        if mode == "r":   # -> if (false)
            ...
```

### What was implemented

**Sema** (`statements.py`): `_filter_union_codegen_facts` extended to pass
`LiteralType` narrowing facts through to codegen (alongside `UnionType`).

**Codegen context** (`context.py`): Added `literal_facts: dict[str, TpyType]`
tracking active literal narrowing. Save/restore at branch boundaries.

**Codegen statements** (`statements.py`): `_emit_isinstance_extractions`
skips `LiteralType` (no `std::get` needed) but pushes them into
`literal_facts`. Literal facts are saved before entering a branch body
and restored after.

**Codegen expressions** (`expressions.py`): `_try_fold_literal_comparison`
checks if one side of `==`/`!=` is a variable with a single-value
`LiteralType` and the other is a matching literal. Emits `true`/`false`.

**Future**: Emit only the then-body when condition folds to `true`, skip
the branch entirely when `false` (same pattern as
`_gen_if_overload_specialized` for union isinstance). Low priority since
the C++ compiler already optimizes away constant branches.


## Phase 3b: Literal Overload Flattening (Done)

Per-literal function/method specialization for `@overload` groups.
Every literal stub gets its own name-mangled C++ function with dead
branch elimination. Enables different return types per literal value.

### What was implemented

**Name mangling** (`functions.py`): `literal_mangled_name(base, stub)`
produces `{name}__lit_{sanitized_values}`. Values sanitized for C++
identifiers. Non-literal fallback stubs keep the original name.

**Codegen -- functions** (`functions.py`): When `_overload_stubs_are_literal_only`
returns True, `_gen_literal_specialized_function` emits per-stub
specializations using impl's body with stub's return type.
`literal_overload_facts` injected for dead branch elimination (survives
`reset_scope`, same pattern as `overload_param_types`). Unreachable
returns (dead code after branch elimination) are silently skipped.

**Codegen -- methods** (`records.py`, `functions.py`): Parallel
`_gen_literal_specialized_method` creates a synthetic `TpyFunction` with
mangled name, impl's body, and stub's return type. Same dead branch
elimination via `literal_overload_facts`.

**Forward declarations** (`functions.py`): Per-stub forward decls use
impl's C++ param types with stub's return type and mangled name.

**Call-site dispatch** (`expressions.py`): When `resolved_function_info`
has `LiteralType` params and the call is to an overloaded function,
`literal_mangled_name` produces the mangled C++ name. Works for both
free functions and method calls. Cross-module calls use qualified
mangled names.

**Dead branch elimination in specializations**: `_gen_if_overload_specialized`
extended to resolve literal equality conditions statically via
`_resolve_literal_eq_statically`. Combined with `literal_overload_facts`,
the specialization bodies have all branches except the matching one
eliminated. Unreachable returns after eliminated branches are silently
dropped.

### Example

```python
@overload
def get_field(name: Literal["age"]) -> Int32: ...
@overload
def get_field(name: Literal["name"]) -> str: ...
@overload
def get_field(name: str) -> Int32 | str: ...
def get_field(name: str) -> Int32 | str:
    if name == "age": return 42
    return "hello"
```

Generated C++:
```cpp
int32_t get_field__lit_age(std::string_view name) { return 42; }
std::string get_field__lit_name(std::string_view name) { return "hello"; }
std::variant<int32_t, std::string> get_field(std::string_view name) { ... }
```

Call sites: `get_field("age")` -> `get_field__lit_age("age")`,
`get_field(var)` -> `get_field(var)`.


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
str, primitive switch for int/bool. `_push_literal_facts` pushes
narrowing facts into `literal_facts` for dead branch elimination within
arm bodies. `_SwitchEntry` extended with `type_facts` for the switch path.

**Operator fix** (`sema/operators.py`): `LiteralType` resolved to
`base_type` in `get_effective_type_for_binop` and `_resolve_pending_types`,
enabling operators like `str + Literal[str]` on narrowed variables.


## Phase 5: Literal Types in Variables

Literal values persist through variable bindings without explicit
annotation.

### Problem

Today, `x = "rb"` produces `StrType` (or `PendingStrType` for locals).
The literal value is lost. To enable `open(file, x)` dispatching to the
binary overload, the variable must carry the literal value in its type.

Note: Phase 3 already makes `Literal`-annotated parameters useful. This
phase extends to unannotated local variables. The practical gap is only
when a literal is assigned to a local without annotation and then passed
to a Literal-dispatched overload.

### Design Options

**Option A: Always produce literal types for string/int/bool literals.**

`analyze_expr(TpyStrLiteral("rb"))` returns `LiteralType(StrType, ("rb",))`
instead of `StrType`. Same for int/bool literals (already done for ints --
`IntLiteralType`).

Pros: Simple, consistent with `IntLiteralType` which already works this way.
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


## Phase 6: Literal as General Type (Future)

### Return Types

```python
def get_mode() -> Literal["r", "rb"]: ...
mode = get_mode()  # mode: Literal["r", "rb"]
```

Requires `LiteralType` in return position. Codegen maps to the base type
(C++ can't encode the constraint). Codegen paths that check
`isinstance(ret_type, StrType)` for return value handling (copy semantics,
`std::string` vs `std::string_view`) must unwrap `LiteralType` to
`base_type` first.

### Fields

```python
class Config:
    mode: Literal["debug", "release"]
```

Stored as the base type in C++. The constraint is compile-time only.

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

1. **Interaction with `PendingStrType`**: If a future phase produces
   `LiteralType` from string literals, when does view inference happen?
   Option: first resolve literal-ness, then do view inference on the
   resolved type. Literal string sources are always view-safe (static
   lifetime) for `StrViewType` resolution, but if the variable is passed
   to a function taking `String` (owned), promotion still applies via the
   existing `PendingStrType`/`ViewVarInfo` machinery.

2. **Cross-function literal propagation**: `def f(mode: Literal["r", "w"])`
   is straightforward (annotation in signature). But inferring literal types
   from callers (`f("r")` implies `mode` could be `Literal["r"]`) is not
   planned -- too complex and fragile.

3. **TypedDict integration**: `Literal` keys in TypedDict
   (`d["name"]` where key is `Literal`) is a separate feature (D19 in
   roadmap) that depends on Literal types but has its own design concerns.

4. **Forwarding `LiteralType` args through calls**: Resolved in Phase 2.
   `_enrich_literal_types` passes through already-`LiteralType` args
   without requiring AST-level literal detection.

5. **Name mangling collisions in flattening**: String `"42"` and int `42`
   would produce the same mangled suffix. The same-tag constraint (all
   values in a `Literal[...]` have the same type) prevents this within a
   single function's overload set, but cross-function collisions need care.
   Consider including the tag in the mangled name: `__lit_str_rb`,
   `__lit_int_42`.
