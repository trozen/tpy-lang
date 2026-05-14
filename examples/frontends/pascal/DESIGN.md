# Turbo Pascal Frontend POC

Status: design draft (no implementation yet).

This document plans a Turbo Pascal frontend plugin for TurboPython, the
first concrete consumer of the frontend plugin API
(`docs/FRONTEND_PLUGIN_DESIGN.md`). Goal: take a typical "kid's
Turbo Pascal program" and run it through tpyc end-to-end.

## Goals

1. **Validate the plugin API.** End-to-end demonstration: source ->
   plugin -> `FrontendModule` -> lowering -> sema -> codegen -> binary.
   Surfaces design issues in the plugin contract before any
   higher-stakes plugin lands.
2. **Run real TP programs.** Tier-1 subset (below) is the target: the
   programs anyone wrote in TP 5/6/7 in the late 80s / early 90s
   should compile and run with output matching CPython where
   semantics align.
3. **Drive `FixStr` completion.** Pascal `string[N]` semantics map to
   TPy's `FixStr[N]` (fixed-capacity, stack-allocated, value-copied,
   length-prefixed, mutable). FixStr's existing TODOs (concat, eq,
   slicing, common methods) get closed as the POC needs them.
4. **Be the canonical reference example** for plugin authors. Lives
   in-tree at `examples/frontends/pascal/`; CI compiles its test
   suite to guard against plugin-API regressions.

## Non-goals

- Full Object Pascal / Delphi compatibility (classes, virtual,
  properties, RTTI, generics, advanced records, operator overloading).
- DOS-era low-level: inline asm, `goto`, direct port I/O, real-mode
  memory, BIOS interrupt invocation. The portable parts of `Crt`
  (text-mode color/cursor/keyboard via ANSI + raw stdin) and `Graph`
  (modern graphics backend) are Tier 2, not non-goals.
- Cross-compatibility with FPC's stricter modes.
- Performance parity with native Pascal compilers; correctness first.

## Scope tiers

### Tier 1 (in for v1)

The subset that runs typical small TP programs. Implementation order
is roughly in milestone order (below).

- **Program structure**: `program Name;` ... `begin ... end.`
- **Declarations**: `const`, `var`, `type` blocks at program /
  procedure / function scope. Local `var` lowers to `VarDecl`
  inline. The frontend IR has no nested-decl statement node, so local
  `const` and local `type` declarations are **hoisted** by the
  translator: each gets a fresh module-level name (mangled with the
  enclosing routine name to avoid collisions) and the local
  references are rewritten to that name. This is invisible to
  Pascal source.
- **Primitives**: `integer`, `real`, `boolean`, `char`, `string` (default
  capacity 255), `string[N]` (custom capacity).
- **Composite types**: fixed-size arrays `array[lo..hi] of T`, records
  with named fields.
- **Subroutines**: `procedure` (no return) and `function` (typed
  return). Value parameters and `var` (by-reference) parameters.
- **Control flow**: `if/then/else`, `while/do`, `for/to/downto`,
  `repeat/until`, `case/of`.
- **Expressions**: arithmetic (`+ - * / div mod`), comparison (`= <>
  < <= > >=`), boolean (`and or not xor`), set membership (`in` --
  Tier 1 only when restricted to enum / small-int sets, otherwise
  Tier 2), string concatenation (`+`).
- **I/O**: `write` / `writeln` / `read` / `readln` against `output` /
  `input`. File-based variants are Tier 2.
- **Both comment styles**: `{ ... }` and `(* ... *)`.
- **Case-insensitivity** for identifiers and keywords (lowered to
  lowercase canonical form during translation).
- **1-based array indexing**, **1-based string indexing** (lowered to
  TPy's 0-based at translator level).
- **Built-ins**: `length`, `inc`, `dec`, `succ`, `pred`, `ord`, `chr`,
  `abs`, `sqr`, `odd`, `random`, `randomize`.
- **Enums**: `type Color = (Red, Green, Blue);`.
- **Subrange types**: `type TByte = 0..255;` -- accepted; lower to
  the underlying type with optional bounds asserts (Tier 2 to make
  the bounds enforcement first-class).

### Tier 2 (planned for v2, after v1 ships)

The same architecture; v1's design must not foreclose any of these.
Most need only translator + runtime additions, not plugin-API or
TPy-core changes.

- **Units** (M11, shipped): `unit`, `interface`, `implementation`,
  `uses`. Maps to TPy modules + star imports; the unit name becomes
  the module name. Polyglot resolution: `uses X;` finds `X.pas` or
  `X.py` in the entry-point directory; `uses py.X;` strips the
  `py.` prefix and resolves `X` as a TPy stdlib / arbitrary Python
  module.
- **Sets** (M12, shipped): `set of T` for any ordinal. Maps to a TPy
  `set[T]`; literal `[a, b, c]` and `[lo..hi]`; `+`/`-`/`*` set ops
  (union / difference / intersection); `in` membership on set-typed
  variables.
- **Subrange enforcement** (M12, M14.5, shipped): insert
  `check_subrange(...)` at assignment sites and at array index
  sites. Always-on (no `{$R+}` / `{$R-}` directive handling).
- **Ranges in `case`** (M12, M14.5, shipped): `case x of 1..5: ...`.
  Integer / Char ranges lower to a guarded `MatchWildcard` arm
  (`case _ if lo <= x <= hi:`); enum-typed ranges expand to one
  `MatchValue` arm per enum member in the closed interval (no
  ordering comparisons on enums required). Scalar labels stay
  structural `MatchValue` for the jump-table form.
- **Pointers** (M15, shipped): `^T` (type) lowers to `Ptr[T]`.
  `New(p)` -> `unsafe_alloc[T]()` + `unsafe_init(p, default(T))`;
  `Dispose(p)` -> `unsafe_drop(p)` + `unsafe_free(p)`. `p^` (read)
  -> `deref(p)`; `p^ := v` (write) -> `unsafe_store(p, 0, v)`.
  `p^.field` parses as a FieldAccess on DerefExpr and lowers to a
  bare `Attr(p, field)` -- TPy's `Ptr[Record]` auto-derefs on
  attribute access, so the C++ shape is `p->field`. `nil` lowers
  to `None`; comparisons against `nil` route through
  `is None` / `is not None` (TPy raw pointers don't define `==`).
  Forward type references within a `type` block work via
  registration in the prescan pass (`PNode = ^Node;` before
  `Node = record ... end;` is fine).
- **File I/O** (M13, M14.5, shipped, text subset): `text` type and
  `Assign(f, name)` / `Reset(f)` / `Rewrite(f)` / `Append(f)` /
  `Close(f)` / `Writeln(f, x)` / `Readln(f, x)` / `Eof(f)`. Backed
  by a `pascal.runtime.io.TextFile` class that slurps the file into
  a per-line buffer on `Reset` and flushes accumulated writes on
  `Close`.
  - `Erase` / `Rename` are blocked on TPy not exposing the `os`
    module (no `os.remove` / `os.rename`).
  - `File of T` (typed binary files) is blocked on the TPy `struct`
    stdlib macro -- the read side (`unpack`, `unpack_from`,
    `calcsize`) is in place, but `pack` / `pack_into` are still
    `TODO` at the top of `lib/tpy/struct.py` (the comment cites the
    need for a statement-expression or buffer-builder pattern).
    Once `pack` lands, `File of <primitive>` and `File of <flat
    record-of-primitives>` are mechanical: the Pascal translator
    knows `T` at translate time and can emit a literal format
    string into `struct.pack` / `struct.unpack`. `File of <record
    containing PStr>` is messier but doable -- ShortString is
    fixed-size (length byte + N bytes), so it round-trips through a
    fixed format too. Tracked here rather than in `BUGS.md` /
    `TODO.md` because the unblock is TPy-stdlib scope, not a
    Pascal-frontend defect.
- **Variant records** (M16, shipped, flatten layout): `record [common-fields;]
  case <tag>: <T> of label: (fields); ... end`. The parent record
  holds the common fields, the discriminant field, and every
  variant case's fields as plain siblings. TP7 doesn't enforce the
  discriminant at runtime either, so the flatten layout matches
  the source-language semantics directly. The translator generates
  a tiny `__init__` that sets the discriminant to the first
  declared label (TPy's field-default validator rejects
  enum-attribute defaults, hence the init). Name collisions across
  variant cases are surfaced as a Pascal-level diagnostic. The
  alternative we tried -- an inner-record-per-variant + union
  payload + `@property` accessors -- hit two TPy codegen
  limitations recorded in `BUGS.md` (class-pattern `as` bind
  pre-declared as `std::optional<T>`, and reassignable record
  locals stored as `std::optional<T>`); both are reachable from
  plain `.py` code and worth fixing independently, but they made
  the type-safe variant codegen impractical for this milestone.
- **Strings beyond ShortString**: longer-than-255, AnsiString,
  PChar interop. Lower priority within Tier 2; FixStr covers the
  kid-program use case.
- **`Crt` unit (portable subset)** (M14, M14.5, shipped, partial):
  color constants (`Black`..`White`), `TextColor`,
  `TextBackground`, `ClrScr`, `ClrEol`, `GotoXY`, `Delay`,
  `Sound`/`NoSound` (no-ops on modern systems), and `ReadKey`
  (bare-name or parens-form both work).  Backed by
  `pascal/lib/crt.py`, a Pascal-frontend stdlib module that emits
  ANSI CSI/SGR escape codes and routes `ReadKey` through TPy's
  `input()` builtin (one-line-at-a-time, since TPy doesn't expose
  raw-mode termios). `WhereX`/`WhereY` are deferred (need a cursor-
  position query round trip + raw stdin); `KeyPressed` is deferred
  (needs non-blocking stdin / termios, which TPy doesn't expose).
- **`Graph` unit**: `InitGraph`, `CloseGraph`, `SetColor`,
  `SetBkColor`, `PutPixel`, `Line`, `Rectangle`, `Circle`, `Bar`,
  `OutTextXY`, `MoveTo`, `LineTo`, `FloodFill`, palette ops.
  Backed by a TPy `pascal.runtime.graph` module that wraps a
  modern graphics layer -- candidates: SDL2 (via TPy native
  interop), or a simpler PNG/SVG snapshot backend for headless
  test runs. Pascal source is unchanged; the runtime swap is
  invisible to it.

### Tier 3 (genuinely out)

- `goto` / labels.
- Inline asm.
- Low-level DOS-isms: `Port[]` / `PortW[]` direct I/O,
  `Mem[]`/`MemW[]`/`MemL[]` real-mode memory access, BIOS
  interrupt invocation, `Intr` / `MsDos` calls. The portable parts
  of `Crt` and `Graph` are Tier 2; the parts that poke video memory
  or BIOS directly are not.
- Object Pascal classes (`class`, `inherited`, `virtual`,
  `override`, properties). Plain `record` covers the kid-program
  surface; full OOP is a separate, much larger project.
- RTTI / generics / advanced records / operator overloading.
- Conditional compilation (`{$IFDEF}`, etc.) -- accepted as
  comments in v1; not interpreted. Not planned for v2 either.

## Pascal -> TPy IR mapping

| Pascal | TPy IR | Notes |
|---|---|---|
| `integer` | `NamedType("Int32")` | configurable via `--default-int`, but plugin emits `Int32` by default for parity with classic TP |
| `real`, `double` | `NamedType("float")` | TP `real` is 6-byte float on x86; we don't preserve that |
| `boolean` | `NamedType("bool")` | |
| `char` | `NamedType("Char")` | |
| `string` (no `[N]`) | `NamedType("FixStr", [IntTypeArg(255)])` | classic TP default |
| `string[N]` | `NamedType("FixStr", [IntTypeArg(N)])` | custom capacity |
| `array[lo..hi] of T` | `NamedType("Array", [TypeTypeArg(T), IntTypeArg(hi - lo + 1)])` | bounds folded; non-1-based bases lower to a translator-managed offset on every index |
| `record ... end` | `Record` with `fields`; no inheritance, no methods | |
| `type C = (A, B, C);` (enum) | `Enum` | |
| `^T` (pointer type) | `PointerType(NamedType(T))` (Tier 2) | TP heap-pointer semantics; `New` allocates, `Dispose` frees |
| `procedure foo(...)` | `Function(return_type=None)` | |
| `function foo(...): T` | `Function(return_type=T)` | |
| `var x: T` (by-ref param) | `Param(type=PointerType(T))` | translator rewrites caller `foo(x)` to `Call(Name("foo"), args=(Call(Name("take_ptr"), args=(Name("x"),)),))`; param uses inside the body read via `Call(Name("deref"), args=(Name("p"),))` for value pointees, or attribute access for record pointees (TPy's implicit pointer-attribute deref) |
| `case x of ... else ...` | `Match` with `MatchValue` patterns | else -> `MatchWildcard` case |
| `for i := lo to hi do` | `ForRange(var=i, start=lo, end=hi, direction=RangeDir.ASC, inclusive=True)` | |
| `for i := hi downto lo do` | `ForRange(var=i, start=hi, end=lo, direction=RangeDir.DESC, inclusive=True)` | |
| `repeat ... until cond` | `RepeatUntil(body=..., cond=...)` | |
| `write(x)` | `Call(Name("write"), args=(x,))` | resolves to a Pascal-runtime `write` overload |
| `writeln(x, y)` | `Call(Name("writeln"), args=(x, y))` | |
| `length(s)` | `Call(Name("len"), args=(s,))` | TPy's `len` works on FixStr |
| `inc(x)` / `dec(x)` | `AugAssign(target=x, op=BinOpKind.ADD/SUB, value=IntLit(1))` | |
| `succ(e)` / `pred(e)` for enums | runtime-helper call | enum-aware |
| `s1 + s2` (string concat) | `BinOp(BinOpKind.ADD, s1, s2)` | requires `FixStr.__add__` |
| `s = t` (string compare) | `Compare(s, ops=(CmpOpKind.EQ,), comparators=(t,))` | requires `FixStr.__eq__` |
| `s[i]` (1-based) | `Subscript(s, BinOp(BinOpKind.SUB, i, IntLit(1)))` | translator subtracts 1 |
| `a[i]` array (lo-based) | `Subscript(a, BinOp(BinOpKind.SUB, i, IntLit(lo)))` | translator subtracts the declared lower bound |
| `^p` (deref expression) | `Call(Name("deref"), args=(p,))` (Tier 2) | reads the pointee value; null-checked by TPy's `deref` |
| `p^.field` (field access through pointer) | `Attr(p, "field")` (Tier 2) | TPy's implicit pointer-attribute deref |
| `New(p)` (allocate) | `Assign((p,), Call(Name("unsafe_alloc"), type_args=(TypeTypeArg(NamedType(T)),)))` (Tier 2) | translator emits `FromImport("tpy.unsafe", ...)`; also requires `unsafe_init` to construct |
| `Dispose(p)` (free) | `ExprStmt(Call(Name("unsafe_free"), args=(p,)))` (Tier 2) | translator emits `FromImport("tpy.unsafe", ...)`; preceded by `unsafe_drop` for non-trivial pointees |

## Translator architecture

The plugin is a single Python package at `examples/frontends/pascal/`:

```
examples/frontends/pascal/
  pascal_frontend.py         # PLUGIN entry, FrontendPlugin subclass
  DESIGN.md                  # this file
  lexer.py                   # token stream
  parser.py                  # recursive-descent, Pascal AST
  ast.py                     # Pascal AST node types
  translate.py               # Pascal AST -> FrontendModule
  builtins.py                # TP builtin signatures (write, writeln, ...)
  runtime/                   # TPy package for Pascal runtime helpers
    __init__.py
    io.py                    # write/writeln/read/readln implementations
    enum_helpers.py          # succ/pred for enums, ord/chr edge cases
```

### Lexer

Hand-written DFA-style scanner. Handles:
- Both comment forms (`{ ... }` and `(* ... *)`), with directive-style
  `{$...}` lexed as comment in v1 (ignored).
- Case-insensitive keywords (lowercased at lex time).
- Numeric literals: integer (`123`, `$FF` hex), real (`3.14`,
  `1.5e10`).
- String literals: `'hello'` with `''` for embedded apostrophes;
  control-character notation `#13#10`.
- Identifiers: lowercased canonical form; warn on shadowing the
  case of a keyword.

### Parser

Recursive-descent. One method per grammar rule. Output is a Pascal
AST (in `ast.py`) -- a thin tree mirroring TP's syntax. Errors
collect as `Diagnostic`s with source locations; parsing continues
where reasonable to surface multiple errors per pass.

### Translator

`translate.py` walks the Pascal AST and emits a `FrontendModule`.
This is where Pascal-specific behaviors get lowered:

- **Case-insensitivity**: every identifier is canonicalized to
  lowercase at lex time; the translator emits the lowercase form.
  Pascal's case-preserved-but-case-insensitive equality means two
  refs to the same name use the same canonical form.
- **1-based array / string indexing**: every `Subscript` emits a
  `BinOp(SUB, i, IntLit(lower_bound))`, where `lower_bound` is 1
  for strings and the declared lower bound for arrays. Constant
  folding in sema reduces literal indexes back to direct subscript.
- **`var` (by-ref) parameters**: function signature gets
  `Param(type=PointerType(T))`; every call site that passes a `var`
  arg wraps the argument in `Call(Name("take_ptr"), args=(arg,))`;
  every use of the param inside the body becomes
  `Call(Name("deref"), args=(p,))` for value-typed pointees, or
  uses TPy's implicit pointer-attribute deref (`Attr(p, "field")`)
  for record pointees. `take_ptr` and `deref` are existing TPy
  builtins (`lib/tpy/tpy/_core/_functions.py`); no IR additions
  needed. This is a syntactic transform the translator owns.

- **Intrinsic imports**: `take_ptr` / `deref` are exported from
  `tpy`, not implicit builtins. Whenever the translator emits a call
  to either, it also adds a `FromImport(module="tpy", names=(...))`
  to the module's imports so sema can resolve the names. Likewise,
  any `tpy.unsafe.*` call (used for `New` / `Dispose` lowering, etc.)
  is paired with a `FromImport(module="tpy.unsafe", names=(...))`.
  The translator deduplicates: at most one import per module per
  origin, even if many call sites use the intrinsic.
- **String semantics**: Pascal's value-copy assignment (`b := a`
  copies) maps to FixStr's `__copy__` (already present). String
  literals lower to `FixStr` constructor calls.
- **Enum identity**: TP enums are integer-backed; succ/pred work via
  ord arithmetic. The translator emits enum classes with backing
  `Int32` and a runtime helper for `succ` / `pred`.
- **Built-in routing**: `write` / `writeln` / `read` / `readln` /
  `length` / `inc` / `dec` / etc. don't map to TPy stdlib directly
  -- they become calls into `pascal.runtime.io` (`writeln` formats
  per TP rules), `pascal.runtime.builtins` (`length` -> `len`,
  `inc` -> `__iadd__`, etc.). The translator emits a `FromImport`
  for each module that's actually used.

### Plugin entry

```python
from tpyc.frontend_plugin import FrontendPlugin, DecoratorEntry, FrontendOutput

class PascalFrontend(FrontendPlugin):
    api_version = 1
    name = "pascal"
    extensions = (".pas", ".pp")
    decorator_manifest = ()      # Pascal has no decorators

    def parse(self, ctx, module_name, file_path):
        source = ctx.read_file(file_path)
        tokens = lex(source, file_path)
        pascal_ast = parse(tokens)
        module, diagnostics = translate(pascal_ast, module_name, file_path)
        return FrontendOutput(module=module, diagnostics=diagnostics)

PLUGIN = PascalFrontend
```

CLI: `tpyc --dsl-plugin examples/frontends/pascal/pascal_frontend.py
hello.pas`.

## Pascal-specific behaviors -- summary table

| Pascal behavior | Where handled |
|---|---|
| Case-insensitivity | Lexer (lowercase canonical form) |
| 1-based array indexing | Translator (subtract lower bound) |
| 1-based string indexing | Translator (subtract 1) |
| String value-copy assignment | FixStr's `__copy__` (no special action) |
| `var` by-ref params | Translator (rewrite to `PointerType` + caller `take_ptr` + body `deref`) |
| TP `real` precision | Mapped to `float` (TP's 6-byte format not preserved) |
| Comment / directive styles | Lexer (both forms; directives ignored in v1) |
| Built-ins (`write`, `writeln`, etc.) | Pascal runtime package + translator routing |
| Enum `ord` / `succ` / `pred` | Pascal runtime helpers |
| `case` of ranges (Tier 2) | Translator expands ranges into multiple `MatchValue` patterns |

## Test strategy

Tests live in `tests/cases/pascal/<group>/<case>/`, mirroring the
existing `tests/cases/` snapshot machinery. Each case directory:

```
tests/cases/pascal/hello/
  src/main.pas              # Pascal source
  expected/
    diag.txt                # expected diagnostics
    output.txt              # expected runtime stdout
    .fingerprints           # source hash for skip cache
```

The compiler invocation is the same as for `.py` cases, except with
`--dsl-plugin examples/frontends/pascal/pascal_frontend.py` and a
`.pas` entry point.

Snapshot policy follows `CLAUDE.md`'s test-case rules: the
`update_snapshots.py` runner regenerates expected outputs on
intentional changes; existing tests' expected outputs are not
touched without explicit user approval.

A small reference suite covers each milestone (below). Final v1
goal: ~30-50 test cases spanning the Tier-1 surface, plus several
"realistic kid program" end-to-end programs (number guesser, simple
calculator, ASCII Mandelbrot, etc.).

## Milestones

Roughly ordered. Each milestone is a meaningful end-to-end vertical
slice: parser changes + translator changes + new tests + any FixStr
TODOs the tier needs.

1. **Hello world.** `program Hello; begin writeln('Hello, World!');
   end.` Parses, lowers, generates C++, links, runs, prints.
   Validates the entire plugin pipeline. Plugin scaffolding +
   minimal lexer/parser/translator + runtime `writeln(StrView)`.
2. **Integer arithmetic + `var`.** Compute and print integer
   expressions. Adds `var` decl, integer literals, arithmetic
   operators, `writeln(Int32)`.
3. **Control flow.** `if/then/else`, `while`, `repeat/until`,
   `for/to/downto`. End-to-end program: print primes / FizzBuzz.
4. **Procedures + functions.** Value parameters, `var`
   parameters, return values. Recursion (factorial, Fibonacci).
5. **Records and arrays.** Fixed-size arrays with declared bounds,
   record types with named fields. Bubble sort an `array[1..N] of
   integer`.
6. **Strings.** `FixStr[N]` for both `string` and `string[N]`,
   indexing, concat, comparison. **FixStr TODOs cleared as
   needed**: `__add__`, `__eq__`, `__getitem__` slicing,
   `length`-equivalents.
7. **Enums + `case`.** Day-of-week / color examples; `case` over
   enums and integers.
8. **`read` / `readln`.** Stdin-driven number guesser. Validates
   read-side runtime.
9. **Realistic program.** ASCII Mandelbrot or similar; covers
   floats + arrays + nested loops + I/O end-to-end.
10. **Tier-1 builtins finish.** `const`, char / hex / `#nn` literals,
    `inc/dec/abs/chr/ord/sqr/odd`, `succ/pred`, `random/randomize`,
    subrange-type aliases, set-membership `in` for literal sets.
11. **Units.** `unit X; interface ... implementation ... end.` and
    `uses Y;` clauses, including the polyglot Pascal/Python mode and
    the `py.X` escape hatch into TPy stdlib.
12. **Language polish.** `case` ranges (`1..5:`), full `set of T`
    with all set ops, subrange bounds-checking at assignment sites.
13. **Text-file I/O.** `text` type, `Assign`/`Reset`/`Rewrite`/`Close`,
    `Writeln(f, x)`/`Readln(f, x)`, `Eof(f)`, backed by a Pascal-
    runtime `TextFile` class with a per-line buffer.
14. **`Crt` portable subset.** Color / cursor / clear-screen via
    ANSI escapes; `Delay`/`Sound`/`NoSound`; `ReadKey`. Shipped as
    `pascal/lib/crt.py`, a Pascal-frontend stdlib module
    discoverable by `uses Crt;` without the user copying anything
    into the project tree.
14.5. **Gap closing.** Items deferred from M12-M14 that didn't
    need new TPy work: enum range labels expanded via the enum's
    member list, subrange enforcement at array index sites,
    `Append(f)` text-file mode, bare-name parameterless function
    calls (`ch := ReadKey;` without parens) via Python-module
    signature ingestion at translate time.
15. **Pointers + forward types.** `^T` / `New` / `Dispose` /
    `p^` / `p^ := v` / `p^.field` / `nil` and forward type
    declarations within a `type` block (the canonical linked-list
    shape).
16. **Variant records.** Pascal `record ... case kind: T of ...
    end`. Flatten layout: parent record carries common fields +
    discriminant + every variant case's fields as siblings.
17. **TP7 cleanup -- everyday features the original DESIGN.md
    glossed over.** Multi-arg `write` / `writeln` (`writeln('x=',
    x, ' y=', y)`); string-field write through compound target
    (`s.name := 'foo'`); `with rec do <stmt>` (bare-Ident
    receiver, single-record); typed-array constants (`const arr:
    array[1..N] of integer = (1, 2, ...)`); nested procedures /
    functions lifted to module-level with mangled names. Deferred:
    procedural types (`type Fn = procedure(x: integer)` -- needs
    a Callable IR node) and typed-record-consts (TPy doesn't
    auto-derive a kwarg ctor when every field has a default).

Each milestone closes with: tests in `tests/cases/pascal/`, snapshot
diagnostics + output + generated C++ checked in, runs green under
CPython where applicable (CPython compatibility is best-effort for
this plugin -- TP semantics don't fully map to CPython, expect
several `no_cpython.txt` markers).

## Open questions

- **CPython compatibility for tests.** TP's semantics (case-insensitive
  identifiers, 1-based indexing, value-copy strings) don't all map
  cleanly to CPython. Most Pascal cases will likely have
  `no_cpython.txt`; a few simple ones may be made compatible via the
  plugin emitting CPython-friendly Python that mimics TP. Decide
  per-test, lean on `no_cpython.txt` when in doubt.
- **Default `--default-int` interaction.** Plugin emits `Int32` for
  Pascal `integer` regardless of TPy's `--default-int` flag. Confirm
  this is the right call; alternative is "respect `--default-int`
  for type inference, but Pascal `integer` is always `Int32`".
- **Pascal numeric overflow semantics.** TP's `integer` overflow is
  modular wrap (`{$Q+}` enables overflow checks). v1 uses TPy's
  default (likely `Int32` wrap-on-overflow); document the mismatch.
- **Real-number literal precision.** TP `real` is 6-byte; we map to
  `float` (8-byte IEEE 754). Edge cases around precision-sensitive
  arithmetic could differ from TP. Acceptable for the POC; document
  the divergence.
- **Subrange types beyond simple aliases.** Tier 1 accepts subranges
  but doesn't enforce bounds at assignment / array index. Tier 2
  could insert asserts; design TBD.
- **Set type.** v1 (Tier 1) accepts `set of T` only when T is enum or
  small-int (folds to a bitmask). Larger sets are Tier 2. Decide:
  emit a `tpy.Set[T]` for Tier 2, or a Pascal-runtime `BitSet`?
- **Plugin lives in-tree as an example.** As `examples/frontends/`
  fills out, decide whether to keep all in-repo or split out
  (especially if the plugin grows beyond ~3-5k LOC).

## Out of scope for this doc

- The frontend plugin API itself (see
  `docs/FRONTEND_PLUGIN_DESIGN.md`).
- TPy's existing FixStr API completion as an independent track --
  here we just consume it as the POC drives requirements.
