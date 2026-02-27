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
| `str` slicing (`s[1:3]`) | Done |
| `str` slice step (`s[::2]`) | Planned |
| `__str__` / `__repr__` dispatch via `str(obj)` and `repr(obj)` | Done |
| f-strings | Done |
| `@noalloc` string restrictions | Planned |
| `@noalloc` warn on unnecessary `string_view` -> `string` copies | Planned |
| `@noalloc` warn on alias that could stay `string_view` | Planned |
| f-string `!s` conversion with format spec (convert then format) | Planned |
| f-string `!r` conversion | Done |
| f-string `int` (BigInt) with format specs (needs `std::formatter<BigInt>`) | Planned |
| f-string `Optional[T]` support (narrowed optional in f-string context) | Planned |
| f-string enum: output name instead of integer value (needs `__str__`) | Planned |
| f-string print optimization (`print(f"...")` -> streaming `<<`) | Planned |
| f-string format spec full Python compatibility (`,` `_` `=` `n` `%` `z`) | Future |
| `FixStr[N]` fixed-capacity string, stack allocated | Future |
| `str` field lifetime safety rules | Future |

## String Methods

Methods available on `str`, `String`, and `StrView` types. All methods are `is_readonly` (no mutation).

| Python method | Status | Notes |
|---------------|--------|-------|
| `len(s)` | Working | `__len__` -> `s.size()` |
| `s[i]` | Working | `__getitem__` -> `tpy::get_char`, returns `Char` |
| `s + t` | Working | `__add__` -> `tpy::str_concat`, returns `String` |
| `s.split()` | Working | Whitespace split -> `list[str]` |
| `s.split(sep)` | Working | Separator split -> `list[str]` |
| `s.split(sep, maxsplit)` | Working | Split with limit -> `list[str]` |
| `s.join(items)` | Working | Join iterable of strings -> `str` |
| `s.strip()` | Working | Whitespace strip, returns `StrView` |
| `s.lstrip()` | Working | Strip from left, returns `StrView` |
| `s.rstrip()` | Working | Strip from right, returns `StrView` |
| `s.replace(old, new)` | Working | Replace all occurrences |
| `s.find(sub)` | Working | Returns index or -1 |
| `s.rfind(sub)` | Working | Reverse find, returns index or -1 |
| `s.index(sub)` | Working | Like find but panics on miss |
| `s.startswith(prefix)` | Working | Returns `bool` |
| `s.endswith(suffix)` | Working | Returns `bool` |
| `s.upper()` | Working | ASCII uppercase |
| `s.lower()` | Working | ASCII lowercase |
| `s.count(sub)` | Working | Count non-overlapping occurrences |
| `s.isdigit()` | Working | All chars are digits (empty=False) |
| `s.isalpha()` | Working | All chars are alphabetic (empty=False) |
| `s.isalnum()` | Working | All chars are alphanumeric (empty=False) |
| `s.isspace()` | Working | All chars are whitespace (empty=False) |
| `s.isupper()` | Working | All cased chars are uppercase (needs cased char) |
| `s.islower()` | Working | All cased chars are lowercase (needs cased char) |
| `s.capitalize()` | Working | Uppercase first, lowercase rest |
| `s.title()` | Working | Titlecase words |
| `s.swapcase()` | Working | Swap upper/lower |
| `s.removeprefix(p)` | Working | Remove prefix, returns `StrView` |
| `s.removesuffix(s)` | Working | Remove suffix, returns `StrView` |
| `s.rindex(sub)` | Working | Like `rfind` but panics on miss |
| `s.splitlines()` | Working | Split on `\n`/`\r\n`, returns `list[str]` |
| `s[i:j]` (slicing) | Working | Returns `StrView`, Python clamping semantics |
| `s[i:j:k]` (slice step) | Not yet | Requires step support |
| `s.format(...)` | Not yet | f-strings planned separately |

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

## F-Strings

F-strings (`f"hello {name}"`) use C++20 `std::format` as the backend.

### AST representation

F-strings are kept structured in the AST as `TpyFString` nodes containing literal
segments and typed expressions (with optional format specs). This structure is NOT
desugared into string concatenation -- codegen needs the full information to choose
the emission strategy based on context:

| Context | C++ codegen | Allocates? |
|---------|-------------|------------|
| Assignment: `s = f"x={x}"` | `std::format("x={}", x)` | Yes |
| Print: `print(f"x={x}")` | `std::format(...)` (currently) | Yes |
| Print optimized (future) | `std::cout << "x=" << x << "\n"` | No |
| FormatString param (future) | `log("x={}", x)` | No |

### Supported features

- String interpolation: `f"hello {name}"`
- Expressions: `f"{a + b}"`
- Format specs: `f"{val:.2f}"`, `f"{n:#x}"`, `f"{n:>10}"`
- `!s` conversion: `f"{x!s}"` (str() on the value)
- `!r` conversion: `f"{x!r}"` (repr() on the value, requires `__repr__`)
- User types with `__str__`: `f"{obj}"` dispatches to `__str__()`
- Brace escaping: `f"{{{x}}}"` -> `{42}`
- Mixed types: int, float, bool, str, Char, fixed ints, BigInt, enum, user records

### Python-compatible formatting

Types that diverge between C++ and Python are pre-converted to match Python
semantics:

| Type | No spec | With spec |
|------|---------|-----------|
| `bool` | `tpy::bool_to_str()` -> "True"/"False" | `static_cast<int>()` -> 0/1 (matches Python: any spec delegates to int) |
| `float` | `tpy::float_to_str()` -> Python-style (e.g. "3.0") | Raw value (C++ semantics) |
| `BigInt` | `.to_string()` | Raw value (C++ semantics) |

### Current limitations

- `!a` conversion is not supported
- Expressions inside format specs (`f"{x:{width}}"`) are not supported
- No `__format__` dispatch on user types
- Print optimization (streaming without allocation) is not yet implemented
- Python-only format spec features are rejected with clear errors:
  `,` and `_` grouping, `=` alignment, `z` option, `n` and `%` type codes

### `@noalloc` interaction (planned)

In `@noalloc` contexts, `f"..."` assigned to a string should be an error (it
allocates). But `print(f"...")` or FormatString passthrough could be allowed since
they don't allocate.

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
