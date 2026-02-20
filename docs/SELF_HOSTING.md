# Self-Hosting Roadmap

What it would take for tpyc to compile itself.

## Status

### Compiler Features

| #    | Feature                        | Effort | Status |
|------|--------------------------------|--------|--------|
| 1.1  | `dict[K, V]`                   | S      | TODO   |
| 1.2  | `tuple` (fixed + variadic)     | S-M    | TODO   |
| 1.3  | `set[T]`                       | S      | TODO   |
| 2.1  | Ternary expressions            | XS     | TODO   |
| 2.2  | Walrus operator (`:=`)         | S      | TODO   |
| 2.3  | Comprehensions (list/dict/set) | S-M    | TODO   |
| 2.4  | `any()`/`all()` + generators   | S      | TODO   |
| 2.5  | Tuple unpacking                | S      | TODO   |
| 3.1  | F-strings                      | M      | TODO   |
| 3.2  | String methods                 | M      | TODO   |
| 3.3  | String slicing                 | S      | TODO   |
| 4.1  | `@dataclass` / record defaults | M      | TODO   |
| 4.2  | Exception classes              | M      | TODO   |
| 4.3  | `Enum`                         | S      | TODO   |
| 4.4  | `@property`                    | XS     | TODO   |
| 4.5  | `@classmethod`                 | XS     | TODO   |
| 5.1  | `try`/`except`/`raise`         | M      | TODO   |
| 6.1  | Default parameter values       | S      | TODO   |
| 6.2  | `Callable` / `std::function`   | M      | TODO   |
| 6.3  | Lambda / closures              | S-M    | TODO   |
| 9.3  | `enumerate()` and `zip()`      | S      | TODO   |

### Source Refactors

| #    | Refactor                                   | Effort | Status |
|------|--------------------------------------------|--------|--------|
| 7.1  | Replace `isinstance` with tag dispatch     | M-L    | TODO   |
| 7.2  | Remove unnecessary `getattr`/`hasattr`     | XS     | TODO   |
| 5.2  | Replace `with` context managers            | S      | TODO   |
| 6.3b | Replace lambdas with named functions       | S      | TODO   |
| 8.3  | Replace `re` with string scanner           | XS     | TODO   |
| 8.4  | Replace `StringIO` with list accumulator   | XS     | TODO   |
| 4.4b | Replace `@property` with method            | XS     | TODO   |
| 4.5b | Replace `@classmethod` with staticmethod   | XS     | TODO   |

### Infrastructure

| #    | Component                      | Effort | Status |
|------|--------------------------------|--------|--------|
| 8.1  | Write own parser               | M      | TODO   |
| 8.2  | `pathlib.Path` or equivalent   | S      | TODO   |
| 8.5  | `subprocess` (spawn processes) | S      | TODO   |
| 8.6  | `tempfile`/`shutil`/`os` wraps | S      | TODO   |

> **Effort key:** XS = hours, S = a day, M = a few days, L = a week+

## Approach

The goal is not to compile the current Python source as-is. Instead, we identify what
language features tpyc needs to gain, and what patterns in the compiler source should
be refactored to be tpyc-friendly. Some Python idioms (like `isinstance` chains and
`getattr`) are code smells anyway — self-hosting is a good forcing function to clean
them up.

Two categories for each gap:

- **Compiler feature** — tpyc needs to support this construct
- **Source refactor** — the compiler source should be rewritten to avoid this construct

---

## 1. New Types

### 1.1 `dict[K, V]` — Compiler feature

Used in 24/56 files. Core data structure for symbol tables, type registries, import
tracking, module exports. Examples:

```python
expr_types: dict[int, TpyType]
imports: dict[str, set[tuple[str, str]] | None | str]
methods: dict[str, list[FunctionInfo]]
```

Maps to `std::unordered_map<K, V>`. Follows the same generic container pattern as
`list[T]`. Needs: construction, `[]` access, `.get()`, `.items()`, `.keys()`,
`.values()`, `.pop()`, `.setdefault()`, `in` operator, iteration.

**Effort:** Small — infrastructure for generic containers already exists.

### 1.2 `tuple` — Compiler feature

Used in 44/56 files. Two distinct use cases:

1. **Fixed-size heterogeneous tuples** — `tuple[str, TpyType]`, `tuple[str, str]` for
   pairs and triples. Used heavily in parameter lists, field info, import tracking.
2. **Variadic homogeneous tuples** — `tuple[TpyType, ...]` for immutable sequences
   (type args, inner types).

Maps to `std::tuple<T...>` for fixed, and a const/frozen vector for variadic. Needs:
construction, unpacking (`a, b = t`), indexing, iteration (for variadic).

**Effort:** Small-medium. Fixed tuples are straightforward. Variadic tuples could be
modeled as `Array` or a frozen list.

### 1.3 `set[T]` — Compiler feature

Used in 12/56 files. For deduplication, membership testing, dependency tracking.

```python
definitely_assigned: set[str]
hoisted_vars: set[str]
```

Maps to `std::unordered_set<T>`. Needs: construction, `.add()`, `.discard()`,
`.remove()`, `in` operator, set comprehensions.

Also: `frozenset` used in 1 place (`LIST_MUTATION_METHODS`). Could be a `const set`
or just an `Array` with linear search (it's small).

**Effort:** Small — same pattern as dict.

---

## 2. Expressions & Control Flow

### 2.1 Ternary expressions — Compiler feature

Used in 26/56 files. Mostly simple:

```python
"true" if expr.value else "false"
cpp_type = "auto" if is_protocol else param_type.to_cpp()
```

Maps directly to C++ `cond ? a : b`.

**Effort:** Tiny.

### 2.2 Walrus operator (`:=`) — Compiler feature

Used in 11/56 files. Primarily for conditional binding:

```python
if proto := module.protocols.get(name):
    ...
if result := self.operators.resolve_binop(left, op, right):
    ...
```

Maps to a declaration + assignment in the condition. C++ supports this natively
(`if (auto x = expr; x)`).

**Effort:** Small.

### 2.3 Comprehensions — Compiler feature

**List comprehensions** (16 files): Mostly simple single-`for`, sometimes with filter:

```python
args = [self._parse_expr(a) for a in node.args]
lines = [line.strip() for line in cpp_code.split("\n") if line.strip()]
```

**Dict comprehensions** (~8 uses):

```python
in_degree = {name: 0 for name in self.modules}
records_by_name = {r.name: r for r in module.records}
```

**Set comprehensions** (~5 uses):

```python
own_field_names = {fld.name for fld in record.fields}
```

All desugar to loops. No nested comprehensions found.

**Effort:** Small-medium — loop desugaring is mechanical.

### 2.4 Generator expressions in `any()`/`all()` — Compiler feature

Used in ~15 places:

```python
if all(int32_range_contains(v) for v in literal_values):
if any(isinstance(ta, TypeParamRef) for ta in param_type.type_args):
```

Can desugar to a loop with early return. Or implement as special forms that take a
lambda predicate.

**Effort:** Small.

### 2.5 Tuple unpacking — Compiler feature

Common in `for` loops with `.items()` and in assignments:

```python
for name, method_def in pdef.methods.items():
for orig_name, local_name in list(import_items):
level_str, loc, partial = body.split(":", 2)
```

Needs destructuring assignment for 2-3 element tuples.

**Effort:** Small.

---

## 3. String Operations

### 3.1 F-strings — Compiler feature

Used in 32/56 files. Mostly simple variable interpolation, occasionally with method
calls or expressions:

```python
f"Type '{name}' not found"
f"{self.to_cpp()}&"
f"tpy_user::{module_name.replace('.', '::')}"
```

Implementation: desugar to string concatenation + `str()` conversion. Complex
expressions in interpolation slots need temporary variables.

**Effort:** Medium — the parsing and desugaring has some edge cases.

### 3.2 String methods — Compiler feature

The compiler uses these `str` methods extensively:

| Method | Usage | C++ mapping |
|--------|-------|-------------|
| `.split(sep)` | Tokenizing, path splitting | Custom or `std::views::split` |
| `.join(iterable)` | Code generation | Loop with separator |
| `.replace(old, new)` | Escaping, template expansion | `std::string` replace loop |
| `.strip()` / `.rstrip()` / `.lstrip()` | Whitespace cleanup | Trim functions |
| `.startswith(prefix)` | File type checks | `std::string_view::starts_with` |
| `.endswith(suffix)` | File type checks | `std::string_view::ends_with` |
| `.find(sub)` | Substring search | `std::string_view::find` |
| `.count(sub)` | Bracket matching (REPL) | Loop |
| `.format(...)` | Template expansion (1 file) | Can replace with f-strings |

Also: method chaining like `value.replace('\\', '\\\\').replace('"', '\\"')`.

**Effort:** Medium — each method is simple but there are many. Can be implemented
incrementally since not all are needed at once.

### 3.3 String slicing — Compiler feature

A few uses:

```python
full_input.rstrip()[:-1]          # Remove last char
source.split('\n')[start:end]     # Sublist (list slicing, not string)
line[1:]                          # Skip first char
```

Most are list slicing (after `.split()`), not string slicing. A handful of string
slices with simple indices.

**Effort:** Small — already planned.

---

## 4. Classes & OOP

### 4.1 `@dataclass` — Compiler feature

Used for 53 class definitions across the compiler. Two patterns:

1. **Frozen dataclasses** (23 uses) — immutable value types. All TpyType subclasses.
   `@dataclass(frozen=True)` with typed fields, some with defaults.

2. **Mutable dataclasses** (30 uses) — AST nodes, context objects, info records.
   Many use `field(default_factory=list)` and `field(default_factory=dict)`.

Features needed:
- Auto-generated `__init__` from field declarations
- `frozen=True` (immutable instances)
- `field(default=...)` and `field(default_factory=...)`
- `__post_init__` (1 use: `Scope` sets `self.depth` from parent)
- `field(kw_only=True)` (2 uses: `TpyExpr.loc`, `TpyStmt.loc`)
- `__eq__` and `__hash__` auto-generation (frozen types)

**Alternative:** Instead of implementing the full `@dataclass` decorator, tpyc could
support a built-in `record` with these features natively — fields with defaults,
frozen modifier, auto `__eq__`/`__hash__`. The compiler source would use plain class
definitions with explicit fields instead of `@dataclass`.

**Effort:** Medium. The "native record with defaults" approach is simpler than
replicating Python's `@dataclass` machinery.

### 4.2 Inheritance from `Exception` — Compiler feature

4 custom exception classes:

```python
class ParseError(Exception): ...
class SemanticError(Exception): ...
class CompileError(Exception): ...
class CodeGenError(Exception): ...
```

Each has a custom `__init__` storing context fields and a `format()` method. All
inherit directly from `Exception`. No deep hierarchies, no multiple inheritance.

Needs: basic single inheritance from a built-in `Exception` base, custom `__init__`.

**Effort:** Tied to exception handling (section 5.1).

### 4.3 `Enum` — Compiler feature

4 enum definitions:

```python
class TypeParamKind(Enum):     # TYPE, INT
class BindingKind(Enum):       # VARIABLE, FUNCTION, RECORD, MODULE, IMPORTED_NAME, BUILTIN
class CoercionContext(Enum):   # ASSIGN, INIT, ARG, RETURN
class DiagnosticLevel(Enum):   # ERROR, WARNING
```

Maps to C++ `enum class`. Needs: definition, member access, comparison. Two use
string values (`"type"`), two use `auto()`.

**Effort:** Small.

### 4.4 `@property` — Compiler feature

1 use in codegen:

```python
@property
def global_scope(self) -> bool:
    return self._scope_depth == 0
```

Maps to a getter method. Low priority — can be refactored to a regular method.

**Effort:** Tiny, or just refactor to a method.

### 4.5 `@classmethod` — Compiler feature

1 use:

```python
@classmethod
def from_env(cls) -> CppCompilerConfig:
    ...
```

Can be refactored to a `@staticmethod` that returns the type.

**Effort:** Tiny, or just refactor.

---

## 5. Error Handling

### 5.1 `try` / `except` / `raise` — Compiler feature

9 try/except blocks in the compiler. Patterns:

1. **Error wrapping** (3 uses): Catch one error type, re-raise as another:
   ```python
   try:
       ast = parser.parse(source)
   except ParseError as e:
       raise CompileError(e.message, module_name, path)
   ```

2. **Error swallowing** (2 uses): Try parsing, ignore failure:
   ```python
   try:
       type_args = self._parse_type_args_from_subscript(node.func)
   except ParseError as e:
       type_args_parse_error = e.message
   ```

3. **Cleanup with `finally`** (3 uses): Restore state after scope changes:
   ```python
   try:
       yield inner_scope
   finally:
       self.ctx.loop_depth -= 1
       self.ctx.current_scope = old_scope
   ```

4. **Catch-all wrapping** (1 use): Wrap unknown errors:
   ```python
   except Exception as e:
       raise ParseError(f"Failed to construct type: {e}", node) from e
   ```

Also: ~35 `raise` statements throughout (ParseError, SemanticError, CompileError,
CodeGenError, RuntimeError, ValueError).

Maps to C++ `try`/`catch`/`throw` with `std::exception` subclasses.

**Effort:** Medium — the mechanism is straightforward, but needs type hierarchy
support for exception classes.

### 5.2 Context managers (`with` statement) — Source refactor preferred

3 uses in production code, all for scope management:

```python
with self.scopes.loop_scope():
    ...
```

These use `@contextmanager` with `try`/`finally`/`yield` — a complex Python feature.

**Refactor:** Replace with explicit `enter_scope()` / `exit_scope()` calls, or a
scope guard pattern. The `with` statement itself is not needed if we restructure
scope management.

**Effort:** Small refactor.

---

## 6. Functions & Callables

### 6.1 Default parameter values — Compiler feature

Pervasive throughout the compiler. Types of defaults used:

```python
def error(self, message: str, node: TpyExpr | None = None) -> SemanticError:
def reset(self, *, global_scope: bool = False) -> None:
```

C++ supports default parameters natively.

**Effort:** Small.

### 6.2 `Callable` types / higher-order functions — Compiler feature

Used in ~10 places for callbacks and dependency injection:

```python
type_match: Callable[[TpyType, TpyType], bool]
codegen: Callable[[str, TpyType, TpyType, CoercionContext], str]
type_factory: Callable[..., TpyType] | None
```

Maps to `std::function<R(Args...)>`.

**Effort:** Medium — needs function type syntax and `std::function` wrapping.

### 6.3 Lambda expressions — Source refactor preferred

~20 lambdas in the compiler. Two patterns:

1. **Type factory callbacks** (trivial):
   ```python
   type_factory=lambda t, n: ArrayType(t, n)
   type_factory=lambda t: ListType(t)
   ```

2. **Codegen callbacks** (simple string formatting):
   ```python
   codegen=lambda e, _a, _b, _c: f"static_cast<int32_t>({e})"
   ```

3. **Type mapping** (pass-through to method):
   ```python
   typ.map_inner_types(lambda t: self.substitute_type_params(t, subst))
   ```

**Refactor:** All can be replaced with named functions or method references. The
factory lambdas are just constructor wrappers. The codegen lambdas can be small named
functions defined near their use site.

If tpyc implements lambdas/closures (mapping to C++ lambdas), the source can stay
as-is. But named functions are clearer anyway.

**Effort:** Small refactor. Or small-medium compiler feature if we want closures.

### 6.4 `*args` / `**kwargs` — Not needed

Not used anywhere in the compiler source. No action required.

---

## 7. Dynamic Dispatch Patterns

### 7.1 `isinstance()` chains — Source refactor

824 calls across 35 files. This is the single largest pattern to address. Five
categories:

#### Category A: AST node dispatch (parser) — Goes away with own parser

The parser currently dispatches on `ast.FunctionDef`, `ast.ClassDef`, etc. from
Python's built-in `ast` module. When we write our own parser, this code gets
rewritten from scratch — these `isinstance` calls simply won't exist.

#### Category B: TpyExpr dispatch (sema + codegen) — Refactor to method dispatch

12-15 branch chains in `analyze_expr()`, `gen_expr()`, and similar methods:

```python
if isinstance(expr, TpyIntLiteral):
    ...
elif isinstance(expr, TpyName):
    ...
elif isinstance(expr, TpyBinOp):
    ...
# 12 more branches
```

**Refactor:** Add a `kind` tag enum to `TpyExpr`, or use method dispatch where each
expression node implements its own `analyze()` / `gen()` method. A tag-based switch
is simpler and keeps analysis logic centralized (rather than scattering it across
20 node classes).

~4-5 dispatch sites to refactor.

#### Category C: TpyType dispatch (sema + codegen) — Refactor to method dispatch

5-12 branch chains in type resolution, compatibility checking, codegen type mapping:

```python
if isinstance(typ, NamedType):
    ...
elif isinstance(typ, (ListType, ArrayType, SpanType)):
    ...
elif isinstance(typ, (PtrType, ConstPtrType)):
    ...
```

**Refactor:** Add methods to `TpyType` subclasses for common operations
(`resolve()`, `substitute()`, `to_cpp()` already exists). For compatibility checking,
a tag-based dispatch table works well.

~15-20 methods to refactor across sema/ and codegen/.

#### Category D: TpyStmt dispatch — Same as TpyExpr

6-8 branch chains. Same refactor approach (tag enum or method dispatch).

#### Category E: Already enum-based — No change needed

`BindingKind` dispatch already uses enum comparison, not isinstance. Fine as-is.

**Total effort:** Medium-large refactor, but it's mechanical and improves code quality
regardless of self-hosting.

### 7.2 `getattr` / `hasattr` — Source refactor

Only ~10 actual uses in compiler code (excluding test harness):

| Pattern | Count | Fix |
|---------|-------|-----|
| `getattr(node, 'loc', None)` on TpyExpr/TpyStmt | 4 | Replace with `node.loc` (field always exists) |
| `hasattr(expr, 'resolved_function_info')` on TpyMethodCall | 3 | Replace with `expr.resolved_function_info` (field always exists) |
| `hasattr(stmt, 'loc')` on TpyStmt | 2 | Replace with `stmt.loc` (field always exists) |
| `getattr(var_type, 'element_type', None)` after isinstance | 1 | Replace with `var_type.element_type` |
| `hasattr(node, 'lineno')` on ast.AST | 4 | Goes away with own parser |

**Refactor:** Trivial — all are unnecessary defensive checks on fields that are
guaranteed to exist by the dataclass definitions. Pure code cleanup.

**Effort:** Tiny.

---

## 8. Standard Library Replacements

### 8.1 `ast` module — Replaced by own parser

The entire `parse/parser.py` module wraps Python's `ast.parse()` and walks the
resulting AST. A self-hosted compiler needs its own parser.

Python's grammar is well-documented and relatively simple. Approach:
- Write a recursive-descent parser targeting TurboPython's subset of Python
- Parse directly to `TpyExpr`/`TpyStmt`/`TpyModule` nodes (skip the `ast.*`
  intermediate representation entirely)
- This also eliminates all Category A isinstance calls

**Effort:** Medium — it's real work but straightforward. The grammar subset is small
(no decorators beyond known ones, no comprehension syntax in the parser itself if
we've desugared them, no async, no match, etc.).

### 8.2 `pathlib.Path` — Compiler feature (minimal)

Used for file path manipulation in 8 files: `.parent`, `.exists()`, `.read_text()`,
`.name`, `.suffix`, `/` operator for joining.

Options:
- Implement a minimal `Path` type wrapping `std::filesystem::path`
- Or use plain `str` with path helper functions

**Effort:** Small.

### 8.3 `re` (regex) — Compiler feature or refactor

1 use in production code:

```python
_TEMPLATE_PLACEHOLDER = re.compile(r"\{(self|\d+)\}")
```

Used in `codegen_cpp/context.py` for C++ template placeholder expansion.

**Refactor:** This specific pattern can be replaced with a simple string scanner
(look for `{`, read until `}`, check content). No regex engine needed.

**Effort:** Tiny refactor.

### 8.4 `io.StringIO` — Compiler feature or refactor

Used in `codegen_cpp/generator.py` for building C++ output strings.

**Refactor:** Replace with `list[str]` accumulator + `"\n".join()`, or a simple
string builder class. No need for full `StringIO`.

**Effort:** Tiny refactor.

### 8.5 `subprocess` — Compiler feature

Used to invoke the C++ compiler (`g++`/`clang++`). Essential for the compile-and-run
workflow.

Needs a way to spawn processes. Maps to `fork`/`exec` or `std::system()` or a
minimal process API.

**Effort:** Small — can start with `std::system()` and improve later.

### 8.6 `tempfile`, `shutil`, `os` — Compiler feature (minimal)

Used for temporary directories (REPL), directory cleanup, environment variables.

- `tempfile.mkdtemp()` — can use `std::filesystem::temp_directory_path()` + mkdir
- `shutil.rmtree()` — `std::filesystem::remove_all()`
- `os.environ.get()` — `std::getenv()`

**Effort:** Small — thin wrappers around C++ stdlib.

### 8.7 REPL-only dependencies — Defer

`readline`, `atexit`, `difflib` are only used by the REPL. The REPL is not needed
for self-hosting — the compiler just needs to read files and emit C++. These can be
deferred indefinitely.

---

## 9. Other Minor Gaps

### 9.1 `from __future__ import annotations` — Not needed

Used in 39/56 files for deferred type annotation evaluation. This is a Python-specific
concern for forward references. tpyc already handles forward references in its own
type system — no equivalent needed.

### 9.2 `typing` module imports — Not needed

`Optional`, `Union`, `TYPE_CHECKING`, `Callable`, `Any` — these are Python's type
annotation helpers. tpyc has its own type syntax (`T | None`, etc.). The `Callable`
type annotation would need a tpyc equivalent if we support higher-order functions.

### 9.3 `enumerate()` and `zip()` — Compiler feature

Used extensively for iteration:

```python
for i, arg in enumerate(args):
for pname, ptype in zip(names, types):
```

Both are common builtins that should be supported. `enumerate` desugars to a counter
variable. `zip` maps to parallel iteration.

**Effort:** Small.

### 9.4 `isinstance()` as a feature — Not needed for self-hosting

After the source refactors in section 7.1, the compiler source won't use `isinstance`.
It can be deferred as a general language feature.

---

## Suggested Order of Attack

Prioritized by: unblocks the most code, builds on existing infrastructure.

### Phase 1: Core types and expressions

These are foundational and unblock almost everything:

1. **`dict[K, V]`** — unblocks 24 files
2. **`tuple` (fixed-size)** — unblocks parameter lists, field info, imports
3. **`set[T]`** — unblocks tracking sets
4. **Ternary expressions** — trivial, unblocks 26 files
5. **Default parameter values** — unblocks most function signatures
6. **F-strings** — unblocks error messages everywhere
7. **`Enum`** — unblocks 4 definitions used throughout

### Phase 2: Iteration and comprehensions

8. **`enumerate()` and `zip()`** — unblocks common iteration patterns
9. **Tuple unpacking** in for loops and assignments
10. **List comprehensions** — unblocks transformation patterns
11. **Dict/set comprehensions**
12. **Walrus operator**

### Phase 3: Error handling and string ops

13. **Exception classes** (inherit from Exception)
14. **`try` / `except` / `raise`**
15. **String methods** (`.split()`, `.join()`, `.replace()`, `.startswith()`, etc.)
16. **String slicing**

### Phase 4: Functions and types

17. **`Callable` types / `std::function`**
18. **Higher-order functions** (passing functions as arguments)
19. **Closures / lambda** (if not refactored away)
20. **`any()` / `all()` with generator expressions**

### Phase 5: Infrastructure

21. **Write the parser** (recursive descent, targeting TpyExpr/TpyStmt directly)
22. **`pathlib.Path`** or equivalent file path handling
23. **`subprocess`** for invoking C++ compiler
24. **Minimal `tempfile` / `os` / `shutil`** wrappers

### Parallel: Source refactors (can start immediately)

These improve code quality now and reduce the self-hosting gap:

- [ ] Remove unnecessary `getattr`/`hasattr` (10 instances, trivial)
- [ ] Add `kind` tag to `TpyExpr`, `TpyStmt`, `TpyType` for dispatch
- [ ] Replace `isinstance` chains with tag-based switches or method dispatch
- [ ] Replace lambdas with named functions
- [ ] Replace `with` context managers with explicit enter/exit
- [ ] Replace `re.compile` with string scanner
- [ ] Replace `StringIO` with string list accumulator
- [ ] Replace `@classmethod` / `@property` with regular methods

---

## What's NOT Needed

Features the compiler source does not use:

- `*args` / `**kwargs` — not used
- `async` / `await` — not used
- `yield` / generators — not used (context managers can be refactored)
- `match` / `case` — not used
- `lambda` — can be refactored away
- Multiple class inheritance — not used (exceptions use single inheritance)
- Metaclasses — not used
- Decorators beyond `@dataclass` and `@staticmethod` — not used
- `eval` / `exec` — not used
- List slicing with step — not used
