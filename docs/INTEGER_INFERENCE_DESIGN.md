# Integer Literal Type Inference

Status: Design (not yet implemented)

## Problem

Bare integer literals default to `BigInt` (arbitrary precision, GMP-backed):

```python
n = 4                    # BigInt
for i in range(n):       # Range<BigInt> -- slow
    print(i)
```

This is correct for CPython compatibility but leaves performance on the table.
Most integer literals in real code are small constants used as loop bounds,
indices, or configuration values -- they would be perfectly served by `int32_t`.

## Current State

**What works today:**

- Explicit annotation: `n: Int32 = 4` gives `int32_t`
- `range(n)` where `n: Int32` picks `Range<int32_t>` (exact type match)

**What doesn't:**

- `range(10)` uses `Range<BigInt>` (bare literals default to BigInt)
- `n = 4; for i in range(n)` uses `Range<BigInt>` because `n` is BigInt
- Any arithmetic on unannotated integer variables goes through GMP

An earlier iteration tried narrowing `range(literal)` to Int32 automatically,
but this was reverted: the loop variable would be Int32, and arithmetic on it
(e.g. `i * i` for large ranges) could overflow where CPython wouldn't. This
violates the "standard Python works out of the box" principle.

## Design Goal

`n = 4` should behave like an "unresolved integer literal" -- each usage site
decides the concrete type based on context. The variable doesn't commit to
BigInt or Int32 at declaration time.

Key principles:

1. **No semantic surprises** -- if Int32 could overflow where BigInt wouldn't,
   keep BigInt. The optimization must be invisible to the user.
2. **CPython compatible** -- code that runs on CPython should produce the same
   results (modulo performance).
3. **Configurable defaults** -- `@noalloc` functions, future build flags, or
   per-module settings may change the default integer type (e.g. Int64).

## Prior Art: Go's Untyped Constants

Go solves a closely related problem with its "untyped constants" mechanism.
Understanding how Go works clarifies both the approach and its limits.

### How Go Does It

Go distinguishes **constants** (`const`) from **variables** (`var`/:=`). Only
constants get deferred typing:

```go
const n = 4          // untyped integer constant -- no committed type yet
var x int32 = n      // n coerces to int32 (value fits)
var y float64 = n    // n coerces to float64 (value fits)
fmt.Println(n)       // n uses default type: int (= int64 on 64-bit)
```

Three properties make this work:

1. **Arbitrary-precision at compile time** -- constant expressions like
   `1e1000 / 1e999` are evaluated with unlimited precision. The spec requires
   at least 256 bits for integer constants. Overflow is checked only when the
   value is assigned to a concrete type.

2. **Default type is the efficient native type** -- untyped integer constants
   default to `int` (int64 on 64-bit), not some safe-but-slow arbitrary
   precision type. Go's philosophy: the native machine word is the right
   default.

3. **Explicit constant/variable distinction** -- `const n = 4` defers typing;
   `n := 4` immediately gets type `int`. No ambiguity about which names
   participate in deferred resolution.

### What We Can Take From Go

| Go concept | TurboPython analog |
|---|---|
| `const n = 4` -- explicitly constant | `n = 4` where prescan shows no reassignment |
| Default type is `int` (int64) -- fast | Default to Int32 (or Int64 with a flag) -- fast |
| Constant folding with arbitrary precision | Compile-time folding for `n = 2 + 3` etc. |
| Type forced at each usage site | Same: `range(n)` picks Int32, `f(n: BigInt)` coerces |
| `n := 4` -- variable, immediately typed `int` | `n = 4; n = f()` -- reassigned, immediately BigInt |

### Where We Differ From Go

- **Go has no BigInt fallback.** Go's `int` can silently overflow at runtime.
  We have BigInt as the safe default, so we can be more conservative -- if
  there's any doubt, fall back to BigInt instead of risking overflow.

- **Go uses explicit `const`.** We infer constness from the prescan (no
  reassignment). This is slightly less explicit but avoids adding a keyword
  and works naturally with Python syntax.

- **Go constants never live in memory.** They're purely compile-time values.
  Our "constant-like" variables are real variables that need a runtime type
  for codegen. The deferred resolution must produce a concrete type before
  code generation.

- **Go's default is int64, ours should probably be Int32.** Go chose the
  native word size. For TurboPython, Int32 covers the vast majority of
  literal use cases (loop bounds, indices, small constants) and keeps
  memory/cache usage tight. Int64 would be a reasonable alternative,
  configurable via build flags.

### Key Insight

Go defers type only for things it *knows* are constant. We have an equivalent
signal: non-reassigned variables initialized from literals. The prescan
(already implemented in `prescan.py`) gives us what `const` gives Go --
certainty that the value won't change at runtime.

## Approach: Deferred IntLiteralType

The compiler already has `IntLiteralType(value)` -- an unresolved type that
carries the literal value and can coerce to Int32, Int64, BigInt, etc. based on
context. Today this type is resolved to BigInt immediately at variable
declaration. The idea is to keep it unresolved longer.

### How It Would Work

```python
n = 4                    # type: IntLiteralType(4), not yet resolved
m = 100                  # type: IntLiteralType(100)

for i in range(n):       # range() overload sees IntLiteralType(4) -> Int32
    print(i)             #   => Range<int32_t>

x = n + m                # arithmetic: both IntLiteralType -> BigInt (safe)
                         #   OR Int32 if we can prove no overflow

y: Int32 = n             # explicit target type -> coerce to Int32
```

Each usage site of an IntLiteralType variable picks the best concrete type:
- `range(n)` -- picks Int32 (literal fits, range iteration is safe)
- `n + m` -- picks BigInt (arbitrary arithmetic could overflow)
- `f(n)` where `f(x: Int32)` -- coerces to Int32
- `print(n)` -- doesn't matter, works with any type

### Resolution Rules

An IntLiteralType variable eventually needs a concrete type for codegen. The
resolution happens in a post-analysis pass:

1. If all usage sites agree on a type -- use that type
2. If usage sites disagree -- use BigInt (safe superset)
3. If the variable is never used -- use BigInt (default)
4. If reassigned from a non-literal source -- use BigInt (can't track value)

### What This Enables

```python
n = 10
for i in range(n):       # Range<int32_t> -- fast
    arr[i] = i * 2       # int32_t arithmetic -- fast

big = 10 ** 100
for i in range(big):     # Range<BigInt> -- compound expr, safe fallback
    print(i)
```

## Interaction with Other Features

### Reassignment

```python
n = 4
n = some_function()      # reassigned from non-literal -> BigInt
```

The pre-scan infrastructure (already implemented in `prescan.py`) identifies
reassigned variables. Reassigned variables should not participate in deferred
resolution -- they commit to BigInt at declaration.

### @noalloc and Build Flags

Future performance profiles could change the default resolution:

```python
@noalloc
def hot_loop():
    n = 4                # resolved to Int32 (or Int64) by default in @noalloc
    for i in range(n):   # Range<int32_t>
        process(i)
```

Build flags like `--default-int=Int64` could change the fallback type globally.
The deferred resolution approach supports this naturally -- the resolution pass
just uses a different default.

### Augmented Assignment

```python
n = 0
n += 1                   # augmented assignment -- keeps IntLiteralType if RHS is literal
n += big_func()          # RHS is not literal -> resolve to BigInt
```

### Top-Level / Globals

Global variables are conservative -- they stay BigInt because any function could
modify them via `global n`. Deferred resolution applies only to function-local
variables.

## Constant Folding

Go evaluates constant expressions with arbitrary precision at compile time.
We should do the same for pure-literal expressions.

Currently, compound literal expressions produce `IntLiteralType(value=None)` --
the value is not tracked, so overload resolution falls back to BigInt:

```python
for i in range(2 ** 16):     # IntLiteralType(None) -> Range<BigInt>
    ...
for i in range(65536):       # IntLiteralType(65536) -> Range<int32_t>
    ...
```

Both calls are semantically identical, but only the second gets the fast path.
With constant folding, the compiler would evaluate `2 ** 16` at compile time:

```python
for i in range(2 ** 16):     # folded to IntLiteralType(65536) -> Range<int32_t>
    ...
```

The folding only applies to expressions composed entirely of integer literals
and safe operators (+, -, *, **, //, %). If any operand is a variable or
function call, the result stays `IntLiteralType(None)`.

This is a separable improvement -- it can be implemented before or after the
main deferred resolution work. Python's `ast` module already constant-folds
some expressions (e.g. `1 + 2` becomes `Constant(3)` in Python 3.12+), but
not all (e.g. `2 ** 16` stays as `BinOp`).

## Implementation Sketch

This is a rough outline; exact details to be worked out during implementation.

1. **Keep IntLiteralType as variable type** -- in `sema/statements.py`, don't
   resolve `IntLiteralType` to `BigInt` for non-reassigned locals. Store it as
   the variable's type.

2. **Propagate through expressions** -- when IntLiteralType appears in
   expressions, let each context decide: `range()` overload picks Int32,
   arithmetic defaults to BigInt, explicit params coerce as needed.

3. **Post-analysis resolution pass** -- after analyzing the function body,
   scan all variables still typed as IntLiteralType. Resolve each based on
   its usage sites. Store the resolved type for codegen.

4. **Codegen reads resolved types** -- no changes needed in codegen if the
   resolution pass updates `var_types` correctly.

## Open Questions

- Should the resolution pass use a "most specific common type" approach or
  just check if Int32 works for all sites?
- How should IntLiteralType interact with generic type inference? E.g.
  `Same(n, x)` where `n` is IntLiteralType and `x` is Int32.
- Negative literals: `n = -4` is `TpyUnaryOp(-, TpyIntLiteral(4))`, not a
  single IntLiteralType. Should the parser or sema fold this into
  `IntLiteralType(-4)`?
- Should the default concrete type be Int32 or Int64? Int32 covers most use
  cases and is more cache-friendly, but Int64 matches Go's choice and avoids
  overflow for larger-but-still-common values.

## Related Work

- **Go's untyped constants** -- numeric literals have no fixed type until used
  in a typed context. Closest analog to what we want. See "Prior Art" section
  above. Reference: https://go.dev/blog/constants
- **Rust's integer inference** -- `let x = 4;` defaults to `i32` but can be
  changed by context. Simpler than our case because Rust has no BigInt and
  defaults are fixed.
- **Swift's ExpressibleByIntegerLiteral** -- literals conform to a protocol
  that lets any type claim them. More general than we need but shows another
  approach to deferred literal typing.
