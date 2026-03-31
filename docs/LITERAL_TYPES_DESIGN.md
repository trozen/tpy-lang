# Literal Types Design

## Overview

`Literal["r", "w"]` enables compile-time dispatch and narrowing based on
known constant values. Phase 1 (done) supports string literals in `@overload`
parameter annotations. This document covers the design for full Literal type
support through narrowing, variables, and general type positions.

## Current State (Phase 1)

Two ephemeral types handle overload dispatch:

- **`LiteralStrType(values: tuple[str, ...])`** -- annotation type in
  `FunctionInfo.params`. Parsed from `Literal["r", "w"]` in `@overload` stubs.
  Behaves like `StrType` for codegen (same C++ representation).

- **`StrLiteralType(value: str)`** -- enrichment type created at call sites
  during overload resolution. When an argument is a `TpyStrLiteral` and a
  candidate has `LiteralStrType` params, the arg type is temporarily enriched
  from `StrType` to `StrLiteralType(value)`. Discarded after matching.

**Resolution order**: Strict pass matches `StrLiteralType` against
`LiteralStrType` only (value in values). Coercion pass matches against
plain `StrType`/`StrViewType` (fallback). This makes Literal stubs preferred
over str stubs regardless of declaration order.

**Limitation**: Only direct string literal arguments dispatch. Variables
always fall through to the `str` fallback:

```python
open("file.txt", "rb")   # -> BinaryIO (Literal match)
mode = "rb"
open("file.txt", mode)   # -> TextIO (str fallback -- value lost)
```

## Phase 2: Unify Types + Integer/Bool Literals

Merge `StrLiteralType` and `LiteralStrType` into a single `LiteralType`,
and extend to accept `int` and `bool` values alongside strings. Also
extend the parser to support `Literal[...]` in all annotation positions
(not just `@overload` stubs).

### Type System

Both Phase 1 types are replaced by a unified `LiteralType`:

```python
@dataclass(frozen=True)
class LiteralType(TpyType):
    """Literal[value1, value2, ...] -- both annotation and enrichment type.

    Single-value instances (from enrichment or narrowing) carry one value.
    Multi-value instances (from annotations) carry the allowed set.
    """
    base_type: TpyType          # StrType, Int32Type, BoolType, etc.
    values: tuple[str | int | bool, ...]
```

This replaces:
- `LiteralStrType(values)` -- now `LiteralType(base_type=StrType, values=...)`
- `StrLiteralType(value)` -- now `LiteralType(base_type=StrType, values=(value,))`

For int literals, the existing `IntLiteralType(value)` already carries the
value -- no new enrichment type needed, just new matching rules:

```python
# In type_matches_strict:
if isinstance(arg, IntLiteralType) and isinstance(param, LiteralType):
    if isinstance(param.base_type, (FixedIntType, BigIntType)):
        return arg.value in param.values
```

### Bool/Int Disambiguation

Python's `True == 1` and `hash(True) == hash(1)`, so `Literal[True]` and
`Literal[1]` are ambiguous if values are stored as plain Python objects.

**Solution**: Store values as `(type_tag, value)` pairs internally:

```python
@dataclass(frozen=True)
class LiteralValue:
    """A typed literal value. Distinguishes True from 1."""
    tag: str           # "str", "int", "bool"
    value: str | int | bool

values: tuple[LiteralValue, ...]
```

The parser determines the tag from the AST node type:
- `ast.Constant(value=True)` where `isinstance(value, bool)` -> tag="bool"
- `ast.Constant(value=1)` where `isinstance(value, int)` -> tag="int"
- `ast.Constant(value="r")` -> tag="str"

Matching checks both tag and value: `IntLiteralType(1)` matches
`LiteralValue(tag="int", value=1)` but NOT `LiteralValue(tag="bool", value=True)`.
`BoolType` literal `True` matches `LiteralValue(tag="bool", value=True)`.

Mixed types in a single `Literal[...]` are rejected at parse time:
`Literal[1, "a"]` is an error. `Literal[True, 1]` is also an error (mixed
bool/int tags).

### Parser

Phase 1 only parses `Literal[...]` inside `@overload` stub parameters.
Phase 2 extends this to ALL annotation positions: regular function
parameters, variable annotations, return types. The parser already handles
`Literal[...]` via `_parse_type_annotation` -- the change is removing any
`@overload`-only restriction if one exists (currently there is none -- the
parser produces `LiteralStrType` regardless of context).

New constant types in annotations:
- `ast.Constant(value: int)` where `not isinstance(value, bool)` -> int
- `ast.Constant(value: bool)` -> bool (check before int, since bool is int subclass)
- `ast.Constant(value: str)` -> str (already supported)
- Negative integers: `ast.UnaryOp(op=USub, operand=ast.Constant(value: int))`

### Enrichment

The enrichment guard in `_enrich_str_literal_types` extends to check for
`LiteralType` params with any base type. Rename to `_enrich_literal_types`.

Also: when an argument already has `LiteralType` (e.g., a `Literal`-annotated
parameter being forwarded to another function), it should match `LiteralType`
params directly without needing AST-level literal detection. This is the key
enabler for Phase 3's forwarding pattern:

```python
def process(mode: Literal["r", "rb"]) -> None:
    open(file, mode)  # mode already carries LiteralType -- matches open()'s Literal params
```

### Migration

All references to `LiteralStrType` and `StrLiteralType` across sema,
codegen, overloads, compatibility, and analyzer are updated to use
`LiteralType`. Key migration points:

- `is_any_str_type()`: check `isinstance(t, LiteralType) and is_str_base(t)`
- `LiteralType` must delegate C++ methods (`to_cpp()`, `to_cpp_param_type()`,
  `to_cpp_param()`, `to_cpp_const_param()`, `param_needs_copy_for_reassign()`)
  to `self.base_type`. For string base: `std::string_view` params. For int/bool:
  the underlying C++ type directly. The `qualified_name()` method must also
  delegate to `base_type` (used by overload exhaustiveness checking).
- `codegen_cpp/functions.py`: `_overload_stubs_are_literal_only` checks
  `isinstance(stub_ptype, LiteralStrType)` -- update to `isinstance(stub_ptype, LiteralType)`.
- `sema/analyzer.py`: overload exhaustiveness check allows `LiteralStrType`
  vs `StrType` -- generalize to `LiteralType` vs `base_type`.

### Effort: M

Type unification touches 10+ files. Int/bool extension is small on top.
The C++ method delegation on `LiteralType` needs care to avoid regressions
in string param codegen.


## Phase 3: Narrowing and Dead Branch Elimination

Equality-based narrowing for `Literal`-annotated parameters, enabling
user-defined functions with Literal dispatch.

### Motivation

With Phase 1, only `@overload` stubs with `@cpp_template` (builtins) can
have different return types per Literal value. User-defined functions emit
a single C++ function, so different return types don't work. But narrowing
enables a different pattern:

```python
def process(mode: Literal["text", "binary"]) -> None:
    if mode == "text":
        # mode narrowed to Literal["text"] -- compiler knows this branch
        handle_text()
    else:
        # mode narrowed to Literal["binary"]
        handle_binary()
```

The narrowing makes `mode == "text"` a compile-time fact within the branch,
enabling dead branch elimination and type-safe dispatch without overloads.

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

### Match/Case on Literal Types

Match statements could exhaustively dispatch on Literal values:

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

### Effort: M-L

Equality narrowing: M (new fact extractor in `_isinstance_facts`).
Codegen fact propagation: S-M (extend `_filter_union_codegen_facts`).
Dead branch elimination: S (constant folding in codegen comparisons).
Match/case: M (subject validation + exhaustiveness + codegen dispatch).


## Phase 4: Literal Types in Variables

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


## Phase 5: Literal as General Type (Future)

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

4. **Forwarding `LiteralType` args through calls**: When a
   `Literal`-annotated parameter is passed to another function that also
   takes `Literal[...]` (e.g., `def forward(mode: Literal["r", "w"]): open(f, mode)`),
   the enrichment logic must recognize already-`LiteralType` args without
   requiring AST-level literal detection. This is addressed in Phase 2's
   enrichment section but is a key correctness requirement.
