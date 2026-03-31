# Literal Types (D7)

## Roadmap

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | `Literal["r", "w"]` string values in `@overload` params | Done |
| 2 | Unified `LiteralType`, int/bool values, `LiteralValue(tag, value)` | Done |
| 3 | Equality narrowing, dead branch elimination | Not started |
| 3b | Literal overload flattening (different return types per literal value) | Not started |
| 4 | `match`/`case` exhaustiveness for Literal subjects | Not started |
| 5 | Literal types in variables (`x: Literal["rb"] = "rb"`) | Not started |
| 6 | General type positions (return types, fields, union flattening) | Not started |

## Future Extensions

| Feature | Notes |
|---------|-------|
| Literal type in `Final` variables | `x: Final = "rb"` infers `Literal["rb"]`. Variant of Phase 5. |
| TypedDict with Literal keys | `d["name"]` where key is `Literal`. Separate feature (D19) with own design. |
| Cross-function literal propagation | Inferring `Literal` from callers. Not planned -- too complex and fragile. |

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


## Phase 3: Narrowing and Dead Branch Elimination

Equality-based narrowing for `Literal`-annotated parameters, enabling
type-safe branching within function bodies. Prerequisite for Phase 3b
(Literal overload flattening with different return types).

### Equality Narrowing

The narrowing system (`narrowing.py`) currently handles `isinstance`,
`is None`, and truthiness. It does NOT handle equality comparisons on
string/literal types.

Adding equality narrowing for Literal types:

```python
mode: Literal["r", "w", "rb", "wb"] = get_mode()

if mode == "rb":
    # Narrow: mode is Literal["rb"] (single value)
    f = open(file, mode)  # -> BinaryIO (literal value known)
else:
    # Narrow: mode is Literal["r", "w", "wb"] (remaining values)
    ...
```

**Implementation**: Add a new `elif` arm inside `_isinstance_facts` for
`TpyBinOp` with `==`/`!=` operators (alongside the existing `&&`/`||`
composition arms). Use `_expr_to_narrowing_key` (already imported from
`prescan.py`) to extract the variable name:

```python
elif isinstance(expr, TpyBinOp) and expr.op in ("==", "!="):
    key = _expr_to_narrowing_key(expr.left)
    if key and isinstance(expr.right, (TpyStrLiteral, TpyIntLiteral, TpyBoolLiteral)):
        var_type = self.effective_type(key)
        if isinstance(var_type, LiteralType):
            value = LiteralValue(tag=..., value=expr.right.value)
            if value in var_type.values:
                true_type = LiteralType(base_type=var_type.base_type,
                                        values=(value,))
                remaining = tuple(v for v in var_type.values if v != value)
                false_type = (LiteralType(base_type=var_type.base_type,
                                          values=remaining)
                              if remaining else var_type.base_type)
                if expr.op == "==":
                    return {key: true_type}, {key: false_type}
                else:
                    return {key: false_type}, {key: true_type}
```

Also handle the reversed form (`"rb" == mode`).

### Overload Resolution with Narrowed Literals

After narrowing, a single-value `LiteralType` variable can dispatch to
Literal overloads:

```python
def process(mode: Literal["r", "rb"]) -> None:
    if mode == "rb":
        f = open("data.bin", mode)  # mode: Literal["rb"] -> BinaryIO
    else:
        f = open("data.txt", mode)  # mode: Literal["r"] -> TextIO
```

The enrichment step recognizes already-`LiteralType` args directly
(from Phase 2's forwarding support) without needing AST-level string
literal detection.

### Dead Branch Elimination

When a `LiteralType` has a single value, equality checks become constants:

```python
mode: Literal["rb"] = "rb"
if mode == "rb":   # always true -- dead branch elimination
    ...
```

**Codegen channel**: The existing `_filter_union_codegen_facts` in
`statements.py` only passes through `UnionType` narrowing facts to codegen.
Literal narrowing facts would be silently dropped. Two approaches:

**Option A**: Extend `_filter_union_codegen_facts` to also pass through
`LiteralType` narrowings. Codegen then checks narrowed type when generating
equality comparisons and emits constant `true`/`false`.

**Option B**: Add a separate codegen context field (parallel to
`ctx.value_ranges` for integers) for literal value facts. Simpler to
reason about, avoids overloading the narrowed_types channel.

Recommend **Option A** -- it's the simpler change and consistent with how
union narrowing already flows through.

### Effort: M

Equality narrowing: M (new fact extractor in `_isinstance_facts`).
Codegen fact propagation: S-M (extend `_filter_union_codegen_facts`).
Dead branch elimination: S (constant folding in codegen comparisons).


## Phase 3b: Literal Overload Flattening

Per-literal function specialization for user-defined `@overload` functions
with different return types. Analogous to union overload flattening but
dispatching on **value** instead of **type**.

### Motivation

With Phases 1-2, only builtins using `@cpp_template` can have different
return types per Literal value (each stub maps to a different C++ function).
User-defined overloads emit a single C++ function, so all stubs must have
the same return type. This limits Literal overloads to value restriction
(compile-time argument validation) but not return type dispatch.

Union flattening already solves this for type-based dispatch: `f(A | B)`
generates `f(A) -> RetA` and `f(B) -> RetB`. Literal flattening extends
this to value-based dispatch.

### Example

```python
@overload
def get_field(name: Literal["age"]) -> Int32: ...
@overload
def get_field(name: Literal["name"]) -> str: ...
@overload
def get_field(name: str) -> Int32 | str: ...

def get_field(name: str) -> Int32 | str:
    if name == "age":
        return 42
    return "hello"
```

### Generated C++

Per-literal specializations with name mangling, plus a fallback for
non-literal args:

```cpp
// Specialization for Literal["age"] -- body with narrowed literal
int32_t get_field__lit_age(std::string_view name) {
    // name == "age" is known true -> dead branch elimination (Phase 3)
    return 42;
}

// Specialization for Literal["name"]
std::string get_field__lit_name(std::string_view name) {
    return "hello";
}

// Fallback for variable args -- full body, union return
std::variant<int32_t, std::string> get_field(std::string_view name) {
    if (name == "age") return 42;
    return "hello";
}
```

Call-site dispatch:
```python
x = get_field("age")    # calls get_field__lit_age, type is Int32
y = get_field("name")   # calls get_field__lit_name, type is str
z = get_field(mode)      # calls get_field, type is Int32 | str
```

### Key difference from union flattening

Union flattening dispatches on C++ type -- each stub has a different
parameter type, so overloaded C++ signatures are naturally distinct.

Literal flattening dispatches on value -- all stubs have the same C++
parameter type (`std::string_view` for str, `int32_t` for Int32, etc.).
Specializations need name mangling to produce distinct C++ functions.

### Implementation

**Sema**: Detect when Literal overload stubs have different return types.
Currently `_overload_stubs_are_literal_only` returns `True` and emits a
single function. When return types differ, mark the function for literal
flattening instead.

**Codegen** (`functions.py`): For each Literal stub group:
1. Emit a name-mangled specialization with the stub's return type
2. Body is the full implementation, but with the literal value injected
   as a narrowing fact (from Phase 3)
3. Dead branch elimination (Phase 3) removes unreachable branches

**Call site** (`calls.py` / `expressions.py`): When the resolved overload
is a Literal stub with a different return type than the fallback, emit a
call to the name-mangled specialization instead of the base function.

**Name mangling**: `{func_name}__lit_{sanitized_value}`. Values are
sanitized for C++ identifiers (e.g., `"rb"` -> `rb`, `42` -> `42`,
`True` -> `true`). Collisions between string and int values sharing
the same representation are prevented by the same-type constraint
(all values in a `Literal[...]` have the same tag).

### Dependencies

- **Phase 3 (narrowing)**: Required for dead branch elimination within
  specializations. Without it, specializations contain the full if/elif
  body but still have the correct (narrower) return type. Functionally
  correct but suboptimal.
- Phase 3 is independently useful (narrowing within single-function
  bodies), so implementing it first makes sense.

### Effort: M

Sema detection: S (extend `_overload_stubs_are_literal_only`).
Name mangling + codegen: M (parallel to union flattening).
Call-site dispatch: S (already have stub -> function routing).


## Phase 4: Match/Case on Literal Types

Match statements can exhaustively dispatch on Literal values:

```python
mode: Literal["r", "w", "rb", "wb"] = ...
match mode:
    case "r" | "w":
        handle_text()
    case "rb" | "wb":
        handle_binary()
    # exhaustive -- no default needed
```

The match codegen already handles string patterns via switch-based dispatch
(for 5+ unguarded cases) or if/elif chains.

**Required changes**:
- `sema/match.py`: `is_primitive` check (used to validate match subjects)
  must include `LiteralType` -- otherwise match on a `Literal`-typed
  subject raises "match subject must be a union, enum, primitive, record,
  or Optional type".
- `codegen_cpp/match.py`: string type check
  `isinstance(subject_type, (StrType, StringType, StrViewType, PendingStrType))`
  must include `LiteralType` with string base.
- Exhaustiveness: validate that all `LiteralType` values are covered by
  case patterns. Missing values produce a compile-time error.

### Effort: M

Subject validation + exhaustiveness + codegen dispatch.


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
