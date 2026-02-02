# String Handling Design

## Goals

- Keep `str` as a non-owning view for fast argument passing and zero allocations.
- Provide an explicit owning string type for safe storage and return values.
- Allow implicit widening from `str` to owning strings in normal code, but forbid it in `@noalloc` contexts.
- Keep the model predictable and explicit where allocations can happen.

## Types

- `str`
  - C++: `std::string_view`
  - Non-owning view. Safe only when the referenced storage outlives the view.

- `DynStr` (owning)
  - C++: `std::string`
  - Owns its storage, safe to store, return, and concatenate.

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

- `-> str` accepts only view-safe expressions (literals, params, globals, `str(DynStr)` views).
- `-> DynStr` accepts any string expression (concat, `str(int)`, etc.).

## Class Members and Lifetime Safety

Storing `str` in class members is risky because `str` is a non-owning view. Without explicit lifetime
tracking, a `str` returned from a function cannot be assumed safe to store in a field.

### Options

1) Conservative rule (simple, safe)
   - Allow storing into `str` fields only from known long-lived sources.
   - Disallow storing `str` returned from functions unless proven long-lived.

2) Lifetime qualifiers (more expressive)
   - Add `str` lifetimes (e.g., `str@static`, `str@borrowed(param)`).
   - Allow storing only `str@static` in fields.

3) Split types
   - Introduce `StaticStr` type for guaranteed static storage.
   - Use `StaticStr` for fields when safe, otherwise use `DynStr`.

4) Require owning fields
   - Disallow `str` fields entirely (or restrict to explicit static cases).
   - Use `DynStr` for all member storage.

### Draft Rule Table (Option 1)

Target: `self.field: str`

Allowed sources:
 - String literals
 - Global/static `str`
 - `self.owned_field: DynStr` (store view of owned storage)
 - `self.field: str` (self-assignment)

Disallowed sources:
 - Any `str` returned from function calls
 - Concatenation results (`str + str`, `str + DynStr`, etc.)
 - `str(int/float/BigInt)` and any other converting constructor
 - Local `str` variables (unless proven static)

## `@noalloc` Behavior

- Implicit `str` -> `DynStr` is disallowed.
- `DynStr(...)` and `str(int/float/BigInt)` are disallowed.
- `str` operations that allocate (concatenation) are disallowed.

## Future: Fixed-Storage Strings

We can add fixed-capacity owning strings for no-alloc or bounded-alloc contexts.

Potential options:

- `FixStr[N]` generic: `FixStr[128]`
- Named aliases: `FixStr128`, `FixStr256`, etc.

Behavior:

- Like `DynStr`, but storage is inline and fixed-size.
- Concatenation and numeric formatting must check capacity and fail/panic or error at compile time when provable.
- Useful for `@noalloc` code with bounded sizes.
