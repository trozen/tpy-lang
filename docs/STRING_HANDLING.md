# String Handling Design

## Status

| Feature | Status |
|---------|--------|
| `str` as `const char*` / `std::string_view` | Done |
| `str` literals, parameters, basic operations | Done |
| `DynStr` owning string type (`std::string`) | Planned |
| `str` -> `DynStr` implicit coercion | Planned |
| `str(numeric)` returning `DynStr` | Planned |
| `@noalloc` string restrictions | Planned |
| `FixStr[N]` fixed-capacity strings | Planned |
| `str` field lifetime safety rules | Planned |

## Goals

- Keep `str` as a non-owning view for fast argument passing and zero allocations.
- Provide an explicit owning string type for safe storage and return values.
- Allow implicit widening from `str` to owning strings in normal code, but forbid it in `@noalloc` contexts.
- Keep the model predictable and explicit where allocations can happen.

## Relationship to Ownership Model

Both `str` and `DynStr` are **value types** in the ownership model (see `docs/OWNERSHIP_DESIGN.md`). They are not pointer variables — they are stored directly in local variables, fields, and containers. Assignment copies the value, no warnings needed.

This is correct because Python strings are immutable — copy-vs-share is unobservable. The compiler optimizes to a move when the source is dead.

Key consequence: `s += gen_str(...)` in a loop is plain in-place `std::string::operator+=`. No aliasing concerns, no dead slot accumulation, no special optimization needed.

## Types

- `str`
  - C++: `std::string_view`
  - Non-owning view. Safe only when the referenced storage outlives the view.
  - Value type — 16 bytes, copies trivially.

- `DynStr` (owning)
  - C++: `std::string`
  - Owns its storage, safe to store, return, and concatenate.
  - Value type — ~32 bytes (SSO), copies on assignment, move when source is dead.
  - For hot paths where DynStr copies are too expensive, use `str` (zero-copy view) or `FixStr[N]` (stack-allocated, bounded).

## Conversions

### `str(...)` builtins

- `str()` -> empty `str` (view of a static empty string)
- `str(str)` -> `str` (identity)
- `str(Char)` -> `str` (view of static char table)
- `str(Bool)` -> `str` (view of static "True"/"False")
- `str(Int32)` -> `DynStr`
- `str(int)` (BigInt) -> `DynStr`
- `str(float)` -> `DynStr`

Rationale: numeric conversions must allocate to be safe, so they produce `DynStr`.

### `DynStr(...)` constructors

- `DynStr(str)` -> `DynStr` (alloc; may use SSO)
- `DynStr(DynStr)` -> `DynStr` (copy)
- `DynStr(Char/Bool/Int32/BigInt/float)` -> `DynStr` (alloc; may use SSO)

### Implicit coercions

- `str` -> `DynStr`: allowed in normal code (implicit allocation).
- `DynStr` -> `str`: allowed (view of owned storage).
- In `@noalloc` contexts: `str` -> `DynStr` is an error.

## Operators

- `str + str` -> `DynStr`
- `str + DynStr` -> `DynStr`
- `DynStr + DynStr` -> `DynStr`

Rationale: concatenation produces new storage, so it returns `DynStr`.

## Local Type Deduction

For unannotated locals:

- If the initializer or any later assignment requires ownership
  (e.g., concatenation, `str(int)`, `DynStr(...)`), infer `DynStr`.
- Otherwise infer `str`.

For annotated locals:

- `s: str = s + "x"` is a type error (must be `DynStr` or explicitly convert).

Examples:

- `s = "abc"` -> `str`
- `s = s + "x"` -> `DynStr`
- `s = str(123)` -> `DynStr`

## Function Parameters

- `str` params accept `str` and `DynStr` (implicit view).
- `DynStr` params require `DynStr` (unless explicitly converted).

## Return Types

- `-> str` returns a view — must reference data that outlives the function (literals, parameters, globals). This follows the same pattern as returning `T` (by reference) in the ownership model.
- `-> DynStr` returns owned data. This follows the same pattern as returning `Own[T]` (by value) in the ownership model.

## Class Members and Lifetime Safety

The ownership model specifies that record fields store values inline.

- **`DynStr` fields**: Safe — owns its storage inline. No lifetime concerns.
- **`str` fields**: Risky — stores a `string_view` inline, which is a non-owning view referencing external data. This is analogous to `Ptr[T]` fields in the ownership model: the view itself is stored inline, but it doesn't own what it points to.

### Rules for `str` fields

`str` fields follow conservative lifetime rules. Only known long-lived sources are allowed:

Allowed sources:
 - String literals
 - Global/static `str`
 - `self.owned_field: DynStr` (view of owned storage)
 - `self.field: str` (self-assignment)

Disallowed sources:
 - Any `str` returned from function calls
 - Concatenation results (`str + str`, `str + DynStr`, etc.)
 - `str(int/float/BigInt)` and any other converting constructor
 - Local `str` variables (unless proven static)

For fields that need to store arbitrary string data, use `DynStr`.

## `@noalloc` Behavior

- Implicit `str` -> `DynStr` is disallowed.
- `DynStr(...)` and `str(int/float/BigInt)` are disallowed.
- `str` operations that allocate (concatenation) are disallowed.

## Future: Fixed-Storage Strings

Fixed-capacity owning strings for `@noalloc` or bounded-alloc contexts.

Potential options:

- `FixStr[N]` generic: `FixStr[128]`
- Named aliases: `FixStr128`, `FixStr256`, etc.

Behavior:

- Like `DynStr`, but storage is inline and fixed-size.
- Concatenation and numeric formatting must check capacity and fail/panic or error at compile time when provable.
- Useful for `@noalloc` code with bounded sizes.
