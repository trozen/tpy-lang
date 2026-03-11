# @overload Dispatch Flattening (B10)

## Overview

Support Python's `@overload` decorator (PEP 484) to generate separate C++ overloads
from a single implementation function. The compiler specializes the implementation body
per overload stub by resolving isinstance/match checks at compile time and eliminating
dead branches.

```python
from typing import overload

@overload
def describe(x: Dog) -> str: ...
@overload
def describe(x: Cat) -> str: ...
@overload
def describe(x: Bird) -> Int32: ...
def describe(x: Dog | Cat | Bird) -> str | Int32:
    if isinstance(x, Dog):
        return "woof: " + x.name
    elif isinstance(x, Cat):
        return "meow: " + x.name
    else:
        return x.wing_count
```

Generated C++:

```cpp
std::string describe(const Dog& x) {
    return std::string("woof: ") + x.name;
}

std::string describe(const Cat& x) {
    return std::string("meow: ") + x.name;
}

int32_t describe(const Bird& x) {
    return x.wing_count;
}
```

No union wrapper function is emitted. Each stub becomes a standalone C++ overload.

## Design Principles

1. **Standard Python syntax**: Uses `@overload` from `typing`, same as mypy/pyright
2. **CPython compatible**: In CPython, `@overload` stubs are ignored at runtime; the
   implementation body handles all cases via isinstance dispatch
3. **Explicit**: Only methods/functions with `@overload` stubs are flattened -- no
   automatic pattern detection
4. **Exhaustive**: Stub parameter types must collectively cover all union variants
5. **Flexible body**: The implementation body can use isinstance, match/case, or any
   other dispatch pattern -- the compiler resolves statically and eliminates dead branches

## Scope

Works for:
- Free functions
- Instance methods
- Static methods
- Generic functions/methods (type params preserved per overload)

Initial scope is union-typed parameters only. Non-union overloads (e.g., `Int32` vs
`float` via coercion) are a future extension.

## Syntax and Semantics

### Declaration

```python
from typing import overload

# Stubs: declare per-overload signatures
@overload
def f(x: A) -> R1: ...
@overload
def f(x: B) -> R2: ...

# Implementation: union-typed, contains the dispatch logic
def f(x: A | B) -> R1 | R2:
    if isinstance(x, A):
        return make_r1(x)
    else:
        return make_r2(x)
```

Rules:
- Stubs must have body `...` (Ellipsis) or `pass`
- Exactly one non-stub implementation with the same name must follow the stubs
- The implementation's union-typed parameters must be supertypes of each stub's params
- Stubs can have different return types (the key use case)

### Exhaustiveness

For each parameter position where the implementation has a union type, the compiler
checks that stub types collectively cover all union members:

```python
# Implementation param: Dog | Cat | Bird
# Stub 1 param:        Dog
# Stub 2 param:        Cat
# Coverage:            {Dog, Cat}
# Missing:             {Bird}
# -> error: @overload stubs for 'describe' don't cover all variants
#           parameter 'x': missing Bird
```

Rules:
- Only parameters where the implementation type is a union are checked
- Each stub parameter must be a concrete (non-union) type or match the implementation
  type exactly. Partial union stubs (e.g., `Dog | Cat` when impl has `Dog | Cat | Bird`)
  are a future extension (see below).
- Stub types must be subsets of the implementation's union members
- Stub parameter names must match implementation parameter names
- Parameters identical across all stubs and implementation are not checked
- Coverage is checked per-parameter independently (no cross-product requirement)
- Return types are NOT checked for exhaustiveness -- each overload's live return
  paths are validated against its declared return type during codegen

### Multiple union parameters

```python
@overload
def f(a: A, b: X) -> R1: ...
@overload
def f(a: B, b: Y) -> R2: ...
def f(a: A | B, b: X | Y) -> R1 | R2:
    ...
```

Coverage: `a` covered by {A, B}, `b` covered by {X, Y}. Passes.

The combination (A, Y) has no stub, so `f(a_val, y_val)` where `a: A, b: Y` would
fail with "no matching overload" at the call site. This is intentional -- the user
chose not to support that combination.

### Call resolution

At call sites, the compiler resolves against **stub signatures** (not the implementation).
The existing two-pass overload resolution engine (`sema/overloads.py`) handles this.

```python
d = Dog("Rex")
result = describe(d)  # resolves to stub 1: describe(Dog) -> str
# result type: str (not str | Int32)
```

## Implementation Plan

### Phase 1: Parser + Module

**`modules/typing.py`**:
- Export `overload` as a known decorator name

**`parse/nodes.py`**:
- Add `is_overload_stub: bool = False` to `TpyFunction`

**`parse/parser.py`**:
- Recognize `@overload` decorator (from `typing` import)
- Set `is_overload_stub = True` on the function
- Validate stub body is `...` or `pass` (error otherwise)

### Phase 2: Sema -- Stub Collection and Validation

**`sema/registration.py`** (method registration) and **`sema/analyzer.py`** (free functions):

Stub grouping:
- When registering functions/methods, group consecutive `@overload` stubs with the
  same name, followed by the implementation
- Error if stubs exist without an implementation
- Error if implementation has `@overload`
- Error if stubs are not contiguous (other functions between stubs of the same name)

Validation:
- For each union-typed parameter in the implementation, check that stub types cover
  all union members
- Store the overload group: list of (stub `TpyFunction`, param type mapping) + the
  implementation `TpyFunction`

Registration:
- Register one `FunctionInfo` per stub (with stub's param types and return type)
  in `RecordInfo.methods[name]` or the global function registry
- The implementation is NOT registered as a callable -- only stubs are visible
  to callers
- Store a back-reference from each stub to the implementation `TpyFunction` and the
  parameter specialization map (which union param maps to which concrete type)

**`sema/analyzer.py`**:
- Analyze the implementation body normally (union-typed params, union return type)
- Skip analysis of stub bodies (they contain only `...`)

### Phase 3: Sema -- Call Resolution

**`sema/calls.py`** and **`sema/methods.py`**:
- User function/method calls with overload stubs use `resolve_overload()` against
  stub `FunctionInfo` entries
- Return type comes from the matched stub
- Error messages on no-match list available stubs

This mostly works already -- `RecordInfo.methods` is `dict[str, list[FunctionInfo]]`
and the overload resolution engine handles multiple entries.

### Phase 4: Codegen -- Overload Specialization

This is the core new work.

**`codegen_cpp/functions.py`**:

For each overload group:
- Iterate stubs (not the implementation)
- For each stub, call `gen_method_def()` / `gen_function_def()` with:
  - The stub's parameter types and return type for the C++ signature
  - The implementation's `TpyFunction` body for code generation
  - An **overload specialization context**: `dict[str, TpyType]` mapping parameter
    names to their concrete types in this overload

For methods, each stub generates its own const/non-const overload pair where
applicable (composing with the existing dual-overload pattern).

**`codegen_cpp/statements.py`** -- Dead branch elimination:

When an overload specialization context is active:

1. **isinstance checks**: `if isinstance(x, T)` where `x` is specialized to type `U`:
   - `U == T` or `U` is a subtype of `T`: always true -> emit only then-branch
   - `U` and `T` are disjoint: always false -> skip then-branch, emit else
   - For elif chains: evaluate each condition, emit the first always-true branch,
     skip always-false branches

2. **match/case**: `match x` where `x` is specialized:
   - Only emit the arm whose pattern matches the concrete type
   - Wildcard/else arms are emitted if no earlier arm matches

3. **Nested dispatch**: If the body has nested isinstance checks (on other variables
   that depend on the specialized param), those are handled normally (they may still
   be dynamic)

**`codegen_cpp/expressions.py`** -- Variable references:

When referencing a specialized parameter:
- Emit the parameter name directly (it's already the concrete type)
- No `std::get<T>()` extraction needed
- No `std::holds_alternative<T>()` checks

**`codegen_cpp/types.py`** -- Return type:

The return type for each overload comes from the stub, not the implementation.
If the stub says `-> str`, the C++ return type is `std::string`, even though the
implementation returns `str | Int32`.

### Phase 5: Validation of specialized bodies

After dead branch elimination for each overload, verify that:
- All live return paths produce a type compatible with the stub's return type
- If a live path returns a type not compatible with the stub, emit an error:
  ```
  error: overload 'describe(Dog) -> str' has a return path producing Int32
  ```

This catches mismatches between the `@overload` declaration and the implementation
logic.

## Codegen Detail: Dead Branch Elimination

The key mechanism is a **specialization map** threaded through codegen:

```python
@dataclass
class OverloadContext:
    """Active when generating code for a specific @overload stub."""
    param_types: dict[str, TpyType]  # param_name -> concrete type
```

### isinstance resolution

```python
def resolve_isinstance_statically(
    self, var_name: str, check_type: TpyType
) -> bool | None:
    """Returns True/False if statically decidable, None if dynamic."""
    if self.overload_ctx is None:
        return None
    concrete = self.overload_ctx.param_types.get(var_name)
    if concrete is None:
        return None
    if concrete == check_type:
        return True
    if isinstance(check_type, UnionType) and concrete in check_type.members:
        return True
    # concrete is a different type entirely
    return False
```

### if/elif/else chain

```python
# Pseudocode for generating an if/elif chain with overload context
for branch in [if_branch, *elif_branches]:
    result = resolve_isinstance_statically(var, branch.check_type)
    if result is True:
        emit_body(branch.body)  # this is the live branch
        return  # skip remaining branches (including else)
    elif result is False:
        continue  # skip this branch entirely
    else:
        emit_conditional(branch)  # dynamic check (non-specialized param)

# If we get here, emit else body (if present)
if else_body:
    emit_body(else_body)
```

### match/case

```python
# Pseudocode for generating match/case with overload context
for arm in match.arms:
    if is_type_pattern(arm.pattern):
        result = resolve_isinstance_statically(subject, arm.pattern.type)
        if result is True:
            emit_body(arm.body)
            return
        elif result is False:
            continue
    elif is_wildcard(arm.pattern):
        emit_body(arm.body)
        return
    # ... other pattern kinds handled normally
```

## Examples

### Method overload (the primary use case)

```python
class Container[T]:
    @overload
    def get(self, index: Int32) -> T: ...
    @overload
    def get(self, name: str) -> T: ...
    def get(self, key: Int32 | str) -> T:
        if isinstance(key, Int32):
            return self._items[key]
        else:
            return self._named[key]
```

```cpp
template<typename T>
struct Container {
    T& get(int32_t key) {
        return _items[key];
    }
    T& get(std::string_view key) {
        return _named[key];
    }
};
```

### Free function with match/case

```python
@overload
def area(s: Circle) -> float: ...
@overload
def area(s: Rect) -> float: ...
def area(s: Circle | Rect) -> float:
    match s:
        case Circle(radius=r):
            return 3.14159 * r * r
        case Rect(width=w, height=h):
            return w * h
```

```cpp
double area(const Circle& s) {
    auto& r = s.radius;
    return 3.14159 * r * r;
}
double area(const Rect& s) {
    auto& w = s.width;
    auto& h = s.height;
    return w * h;
}
```

### Overload with shared logic

```python
@overload
def process(x: Dog) -> str: ...
@overload
def process(x: Cat) -> str: ...
def process(x: Dog | Cat) -> str:
    name = x.name  # shared -- both Dog and Cat have .name
    if isinstance(x, Dog):
        return name + " barks"
    else:
        return name + " meows"
```

Shared code before the dispatch point is emitted in both overloads:

```cpp
std::string process(const Dog& x) {
    auto name = x.name;
    return name + std::string(" barks");
}
std::string process(const Cat& x) {
    auto name = x.name;
    return name + std::string(" meows");
}
```

## Future Extensions

- **Partial union stubs**: A stub could take a partial union (e.g., `Dog | Cat` when
  impl has `Dog | Cat | Bird`), contributing all its members to coverage. Would
  require extending `resolve_overload` to match a concrete arg against union stub
  params via member containment.
- **Non-union overloads**: Overloads distinguished by coercion-compatible types
  (e.g., `Int32` vs `float`, not members of a union). Would need a different
  dispatch mechanism since isinstance doesn't apply.
- **Overload on arity**: Different number of parameters per stub (Python supports
  this with `@overload`). Would map to C++ overloads with different parameter counts.
- **`slice` type integration**: Once a `slice` type exists, `__getitem__` overloads
  with `Int32 | slice` become the canonical use case for user-defined slicing.
