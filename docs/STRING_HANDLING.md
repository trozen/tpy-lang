# String Handling Design

## Roadmap

| Feature | Status |
|---------|--------|
| `str` context-dependent type (param=`string_view`, return/field=`string`) | Done |
| `String` explicit owned type (`const std::string&` params) | Done |
| `StrView` explicit view type (`std::string_view`) | Done |
| `Char` single character type | Done |
| String concatenation (`+`, `+=`) | Done |
| `str()` numeric conversions (`str(42)`) | Done |
| `list[str]` generates `std::vector<std::string>` | Done |
| `Final[str]` generates `constexpr std::string_view` | Done |
| PendingStrType local inference (view vs owned) | Done |
| Alias source tracking with retroactive promotion | Done |
| `string_view` -> `string` codegen for return and init | Done |
| `str` slicing (`s[1:3]`) | Planned |
| `__str__` dispatch via `str(obj)` | Planned |
| f-strings | Planned |
| `@noalloc` string restrictions | Planned |
| `@noalloc` warn on unnecessary `string_view` -> `string` copies | Planned |
| `@noalloc` warn on alias that could stay `string_view` | Planned |
| `FixStr[N]` fixed-capacity string, stack allocated | Future |
| `str` field lifetime safety rules | Future |

## Types

### `str` -- context-dependent

`str` maps to different C++ types depending on context:

| Context | C++ type | Rationale |
|---------|----------|-----------|
| Parameter | `std::string_view` | Borrowed -- no allocation |
| Return type | `std::string` | Owned -- caller gets independent value |
| Class field | `std::string` | Owned -- field outlives any source |
| Local variable | Inferred (see below) | PendingStrType decides |

### `String` (`tpy.String`) -- explicit owned

Always `std::string`. Parameters use `const std::string&` (avoids `string_view` -> `string` temporary construction when caller has a `string`).

### `StrView` (`tpy.StrView`) -- explicit view

Always `std::string_view`. Use when you know the source outlives the variable.

### `Char`

Single character, maps to `char` in C++.

## Local Variable Inference (PendingStrType)

String locals are not immediately assigned a concrete type. Instead, they start as `PendingStrType` and are resolved after the full function body is analyzed.

### Resolution rules

**Resolves to `StrView` (`std::string_view`)** when:
- Initialized from a string literal (`s = "hello"`)
- Initialized from a `str` parameter (`s = name`)
- Initialized from an explicit `StrView` local
- Initialized from a `Final[str]` global constant
- Initialized from a function/method returning `StrView`
- AND no owned-requiring usage is detected

**Resolves to `str` (`std::string`)** when any of:
- Initialized from an owned source (`str(42)`, function returning `str`/`String`)
- Used in augmented assignment (`s += "x"`)
- Passed to a `String` parameter (`const std::string&`)
- Reassigned from an owned source
- Source is another PendingStrType local that resolved to `std::string` (retroactive promotion)

### Alias tracking

When `s2 = s1` where `s1` is a PendingStrType local, `s2` gets its own pending ID with a `source_str_var_id` pointing back to `s1`. After the first resolution pass, a fixup pass checks: if a source resolved to `std::string`, all aliases are retroactively promoted. This prevents dangling `string_view` pointing at a `std::string` that may reallocate.

The fixup handles chains (`a = "x"; b = a; c = b; b += "y"` promotes both `b` and `c`).

### What does NOT trigger promotion

These are safe with `string_view` because C++ handles the conversion at the usage site:
- Concatenation operand (`s + "x"`) -- `tpy::str_concat` takes `string_view` args
- Stored in container (`list.append(s)`) -- C++ constructs `string` at call site
- Assigned to a field (`self.name = s`) -- C++ converts at assignment
- Returned from function (`return s`) -- codegen wraps with `std::string(s)`

## Codegen

### `string_view` -> `string` conversions

C++ requires explicit conversion from `string_view` to `string` in two contexts:

1. **Copy-initialization**: `std::string s = sv;` fails; codegen emits `std::string s = std::string(sv);`
2. **Return**: `return sv;` from a `std::string`-returning function fails; codegen emits `return std::string(sv);`

Assignment (`s = sv;` where `s` is already `std::string`) works implicitly via `operator=`.

## Coercions

| From | To | C++ | Direction |
|------|----|-----|-----------|
| `StrView` | `str` | `std::string(expr)` | Allocates |
| `StrView` | `String` | `std::string(expr)` | Allocates |
| `str` | `StrView` | implicit | Safe (view of owned) |
| `String` | `StrView` | implicit | Safe (view of owned) |
| `Char` | `String` | `std::string(1, expr)` | Allocates |
| `Char` | `StrView` | `tpy::char_to_str(expr)` | Static table |

## Operators

- `str + str` -> `str` (via `tpy::str_concat`, returns `std::string`)
- `str += str` -> in-place `tpy::str_concat` (reassignment)

## Planned: `@noalloc` Warnings

In `@noalloc` contexts, the compiler should warn about unnecessary allocations:

- **Alias could stay `string_view`**: `c = b` where `b` is `std::string` but `b` isn't mutated after `c`'s last use. A `string_view` would avoid the copy. Requires lifetime analysis.
- **Return copy**: Returning a `StrView` local from a `str`-returning function triggers a copy. Similar to `Own[]` return copy warnings.

## Future: Fixed-Storage Strings

`FixStr[N]` -- fixed-capacity owning string for `@noalloc` or bounded-alloc contexts.

- Like `std::string` but storage is inline and fixed-size.
- Concatenation and formatting check capacity, panic on overflow.
- Useful for `@noalloc` code with bounded sizes.

## Future: `str` Field Lifetime Safety

`str` fields store a `string_view` inline -- a non-owning view that doesn't control the lifetime of the referenced data. Conservative rules would restrict allowed sources:

**Allowed**: string literals, global/static `str`, view of an owned field on the same object.

**Disallowed**: function return values, concatenation results, numeric conversions, local variables.

For fields that need to store arbitrary string data, use `String`.
