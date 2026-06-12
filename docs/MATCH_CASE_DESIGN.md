# Match/Case (Structural Pattern Matching)

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Parser + sema + codegen: union subjects with class patterns (keyword field binding), wildcard, capture, as-pattern. Subject narrowing. if/elif codegen. | Done |
| 2 | Positional class patterns (`Point(x, y)`) via `__match_args__` | Done |
| 3 | Literal, singleton (`True`/`False`), and value (`Color.RED`) patterns; enum and primitive subjects | Done |
| 4 | Concrete record subjects (field-value matching), Optional subjects | Done |
| 4b | Optional codegen: `if/else+switch` for Optional enum/primitive, hoisted null check for Optional record | Done |
| 5 | Or-patterns (`Dog() \| Cat():`), guard clauses (`if cond`) | Done |
| 6 | Exhaustiveness warnings (union, enum, Optional, bool) | Done |
| 7 | `switch` codegen for unions (`switch (s.index())`) and enums (`switch (e)`); if/elif fallback when guards present | Done |
| 8 | Optimized string dispatch: for 5+ string literal cases, emit switch-based dispatch. Compiler picks best discriminator (string length or character at position i) that minimizes max bucket size. Also applies to `EnumUtil::try_parse`/`from_name`. | Done |
| 9 | Polymorphic dispatch on `@dynamic`-protocol / polymorphic-class subjects: class patterns lower to `dynamic_cast` / `dyn_adapter_cast`, the subject narrows to the matched subclass inside the arm (reusing the isinstance engine), with wildcard-exhaustiveness + subclass-shadow unreachable rules. | Done |

### Future Extensions

| Feature | Notes |
|---------|-------|
| Sequence patterns `[x, y]` | List/tuple unpacking in patterns; needs sequence protocol |
| Star capture `[x, *rest]` | Sequence with rest binding |
| Mapping patterns `{"k": v}` | Dict matching; needs mapping protocol |
| Nested class sub-patterns | `case Circle(center=Point(x=0)):` -- recursive pattern compilation |
| Builtin type patterns | `case int():` / `case str():` as type checks |
| `Any` subject | `match x:` where `x: Any`; class patterns via `typeid` (reuse the isinstance-on-`Any` path) |
| Or-pattern body dedup | Lambda-based body sharing instead of codegen duplication |
| User-defined `__match_args__` | Explicit override of auto-generated positional mapping |

---

## Overview

PEP 634 structural pattern matching (Python 3.10+) for TurboPython.

Related: `UNION_TYPES_DESIGN.md` (union types, isinstance narrowing),
`FEATURE_ROADMAP.md` B3.

### Goals

- **CPython-compatible**: standard `match`/`case` syntax, same semantics
- **Better than if/elif**: match/case is not just syntactic sugar --
  the compiler exploits pattern structure to emit `switch` statements
  for enums, unions, and primitives (O(1) dispatch), and hoists shared
  checks (e.g. null guard for Optional subjects). Users should expect
  match/case to be at least as fast as hand-written if/elif, and often
  faster.
- **Exhaustiveness checking**: warn when union/enum members are not covered
- **Reuse existing infrastructure**: isinstance narrowing, union codegen,
  enum comparison

### Non-goals (for now)

- Custom `__match_args__` definitions by users (auto-generated only)
- Matching against arbitrary expressions in patterns (only literals, names,
  class patterns)
- `match`/`case` as an expression (Python treats it as a statement)

---

## Python AST Mapping

Python's `ast` module parses `match`/`case` (soft keywords since 3.10) into:

```
ast.Match
  subject: ast.expr            -- the expression being matched
  cases: list[ast.match_case]  -- the case arms

ast.match_case
  pattern: ast.pattern         -- the pattern to match
  guard: ast.expr | None       -- optional guard condition (if ...)
  body: list[ast.stmt]         -- the body to execute on match
```

### Pattern node types

| Python AST node | Example | Meaning |
|-----------------|---------|---------|
| `MatchAs(pattern=None, name=None)` | `case _:` | Wildcard (always matches) |
| `MatchAs(pattern=None, name='x')` | `case x:` | Capture (binds subject to `x`) |
| `MatchAs(pattern=P, name='x')` | `case P() as x:` | Pattern + capture |
| `MatchClass(cls, patterns, kwd_attrs, kwd_patterns)` | `case Circle(radius=r):` | Type check + field binding |
| `MatchValue(value=Constant(...))` | `case 42:` | Literal comparison |
| `MatchValue(value=Attribute(...))` | `case Color.RED:` | Named constant (enum member) |
| `MatchSingleton(value=None)` | `case None:` | Singleton identity check |
| `MatchSingleton(value=True/False)` | `case True:` | Bool singleton |
| `MatchOr(patterns=[...])` | `case Dog() \| Cat():` | Or-pattern (any matches) |
| `MatchSequence(patterns=[...])` | `case [x, y]:` | Sequence unpacking (Phase 2) |
| `MatchMapping(keys, patterns, rest)` | `case {"k": v}:` | Mapping pattern (Phase 2) |
| `MatchStar(name)` | `case [x, *rest]:` | Star capture in sequence (Phase 2) |

---

## Supported Patterns (Phase 1)

| Pattern | AST node | Notes |
|---------|----------|-------|
| `case _:` | `MatchAs` (no name) | Wildcard, default arm |
| `case x:` | `MatchAs` (with name) | Capture, binds subject to name |
| `case P() as x:` | `MatchAs` (pattern + name) | Pattern + capture |
| `case Circle():` | `MatchClass` (no fields) | Type check only |
| `case Circle(radius=r):` | `MatchClass` (keyword) | Type check + field binding |
| `case Point(x, y):` | `MatchClass` (positional) | Positional via `__match_args__` |
| `case 42:` / `case "hi":` | `MatchValue` (constant) | Literal comparison |
| `case Color.RED:` | `MatchValue` (attribute) | Named constant / enum member |
| `case None:` | `MatchSingleton` | None check |
| `case True:` / `case False:` | `MatchSingleton` | Bool singleton |
| `case A() \| B():` | `MatchOr` | Or-pattern |
| `case X() if cond:` | guard on `match_case` | Guard clause |

### Sub-patterns in class patterns

Class pattern fields can themselves be patterns:

```python
case Point(x=0, y=0):      # literal sub-patterns
case Point(x=x, y=_):      # capture + wildcard sub-patterns
case Circle(radius=r):     # capture sub-pattern
```

Phase 1 supports these sub-pattern types within class patterns:
- **Capture**: `field=name` -- binds field value to a local variable
- **Literal**: `field=42` -- checks field equals literal
- **Wildcard**: `field=_` -- matches any value, no binding
- **Value**: `field=Color.RED` -- comparing a field against a named constant
  is **not yet emitted** (rejected with a guard hint; only literal field
  comparisons are supported -- see TODO.md "Open feature gaps")

Deeply nested class sub-patterns (`case Circle(center=Point(x=0)):`)
are deferred to Phase 2.

### Supported subject types

| Subject type | Codegen strategy | Exhaustiveness |
|--------------|-----------------|----------------|
| Union (`A \| B \| C`) | `switch (s.index())` + `std::get<N>` | Yes (all members) |
| Enum | `switch (e)` | Yes (all members) |
| Optional (`T \| None`) | if/elif with `has_value()`/`== nullptr` | Yes (T + None) |
| Primitive (`int`, `str`, `bool`) | if/elif with `==` comparison | No (infinite domain) |
| Record (concrete class) | if/elif on field values; goto for guards | No |
| Polymorphic (`@dynamic` protocol / polymorphic class; bare, `Ptr[Pet]`, or `Box[Pet]`/`Rc[Pet]` deref view) | `dynamic_cast` / `dyn_adapter_cast` if/elif chain; goto for guards | Open hierarchy -- only via wildcard or a root-type arm |

---

## TPy AST Nodes

New AST nodes for the parser to produce:

```python
@dataclass
class TpyMatchStmt(TpyStmt):
    subject: TpyExpr
    cases: list[TpyMatchCase]

@dataclass
class TpyMatchCase:
    pattern: TpyPattern
    guard: TpyExpr | None
    body: list[TpyStmt]
    loc: SourceLocation | None = None

# -- Pattern hierarchy --

@dataclass
class TpyPattern:
    loc: SourceLocation | None = None

@dataclass
class TpyWildcardPattern(TpyPattern):
    """case _:"""
    pass

@dataclass
class TpyCapturePattern(TpyPattern):
    """case x:"""
    name: str

@dataclass
class TpyClassPattern(TpyPattern):
    """case Circle(): / case Circle(radius=r): / case Point(x, y):"""
    cls: TpyExpr                    # class name (TpyName or TpyFieldAccess)
    positional: list[TpyPattern]    # positional sub-patterns
    keywords: list[tuple[str, TpyPattern]]  # (field_name, sub_pattern) pairs

@dataclass
class TpyLiteralPattern(TpyPattern):
    """case 42: / case "hello": / case True:"""
    value: int | float | str | bool | None  # None for case None:

@dataclass
class TpyValuePattern(TpyPattern):
    """case Color.RED: -- named constant via attribute access"""
    expr: TpyExpr  # TpyFieldAccess (e.g. Color.RED)

@dataclass
class TpyOrPattern(TpyPattern):
    """case Dog() | Cat():"""
    patterns: list[TpyPattern]

@dataclass
class TpyAsPattern(TpyPattern):
    """case P() as x:"""
    pattern: TpyPattern
    name: str
```

### Design notes

- `TpyLiteralPattern` covers both `MatchValue(Constant(...))` and
  `MatchSingleton`. Python distinguishes them (singleton uses `is`,
  value uses `==`), but TPy uses `==` for both since C++ value types
  don't have identity semantics.

- `TpyValuePattern` covers `MatchValue(Attribute(...))` -- dotted names
  like `Color.RED` that reference named constants rather than literals.

- `TpyCapturePattern` and `TpyWildcardPattern` are split from `MatchAs`
  for clarity. `MatchAs(pattern=P, name=x)` maps to `TpyAsPattern`.

- Bare names in patterns are **always captures**, never variable lookups.
  This matches CPython semantics: `case x:` binds, `case Color.RED:`
  (dotted name) compares.

---

## Sema Analysis

### Subject analysis

Evaluate the subject expression and determine its type. The subject type
drives pattern validation and codegen strategy:

```python
# In _analyze_match():
subject_type = self._analyze_expr(stmt.subject)
stmt.subject_type = subject_type  # attach for codegen
```

### Pattern analysis

Each pattern is analyzed against the subject type (or narrowed type for
nested patterns). Analysis determines:

1. **Type compatibility**: does the pattern make sense for this subject?
2. **Narrowed type**: what type does the subject have inside the case body?
3. **Bound variables**: what new local variables does the pattern introduce?
4. **Reachability**: is the pattern unreachable (dead code)?

#### Wildcard / capture

Always compatible with any subject type. Capture introduces a new local
binding with the subject's (possibly narrowed) type.

#### Class pattern

For union subjects: check that the class is a member of the union.
Narrow the subject to that member type in the case body.

For concrete subjects: the class must match the subject type. No narrowing
needed (type is already known). Used for field-value matching.

For Optional subjects: class pattern on the concrete type narrows away
None.

Field bindings (keyword and positional): resolve field names against the
matched class, check sub-patterns against field types, introduce local
bindings.

#### Literal / value pattern

Check that the literal type is compatible with the subject type
(e.g., `case 42:` on `int` subject, `case Color.RED:` on `Color` subject).

For union subjects: literal patterns are generally not valid (use class
patterns). Exception: if the union contains a primitive type, a literal
could narrow to that member -- but this is an edge case for Phase 2.

#### Or pattern

Each alternative must be compatible with the subject type. All alternatives
must bind the **same set of variables** with compatible types (CPython
requirement). The narrowed type is the union of all alternative narrowings.

#### As pattern

Analyze the inner pattern, then bind the matched value to the given name.

### Positional class patterns and `__match_args__`

CPython uses `__match_args__` (a class attribute tuple) to map positional
pattern arguments to field names:

```python
@dataclass
class Point:
    x: Int32
    y: Int32
    # CPython auto-generates: __match_args__ = ("x", "y")

match p:
    case Point(0, 0):   # positional: maps to x=0, y=0 via __match_args__
        print("origin")
```

**TPy behavior**: all records have implicit `__match_args__` derived from
field declaration order. This is a minor convenience over CPython (where
only `@dataclass` auto-generates it and regular classes need explicit
`__match_args__`), but it's consistent with TPy's philosophy -- field
order is always known at compile time.

The compiler resolves positional patterns to keyword patterns during sema
by looking up the record's field order. Too many positional args is an
error. Mixing positional and keyword is allowed (positional first, then
keyword, matching CPython).

### Exhaustiveness checking

After analyzing all cases, check whether all possible values of the subject
type are covered.

**Union types**: every member type must appear in at least one case's class
pattern, OR a wildcard/capture case must exist. Emit a **warning** for
non-exhaustive match (valid Python, but likely a bug).

**Enum types**: every enum member must appear in at least one case's value
pattern, OR a wildcard/capture case must exist. Same warning.

**Optional types**: both sides must be covered independently -- a class
pattern covers every non-None value but never None; `case None:` covers
only the None side; wildcard/capture cover both. The warning names the
missing side(s) (`missing: None, Point`).

**Primitive types** (`int`, `str`, `float`): infinite domain, so literal
arms can never prove coverage -- a match without an unconditional
catch-all arm warns (`no unconditional catch-all arm`) and falls through
on the no-match path (no `std::unreachable()` tail).

**Concrete record types**: no exhaustiveness check (only field-value
matching, always needs a default).

The diagnostic includes the missing members:
```
warning: match is not exhaustive; missing case for: Bird
```

### Guard analysis

Guard expressions are analyzed in a scope where pattern bindings are
visible. The guard must produce a `bool`. Guards don't affect
exhaustiveness -- a guarded case is never considered exhaustive for its
pattern (the guard might be false at runtime).

### Or-pattern variable consistency

All alternatives in an or-pattern must bind the same variable names with
compatible types:

```python
case Dog(name=n) | Cat(name=n):   # ok: both bind 'n: str'
case Dog(name=n) | Cat():          # error: 'n' not bound in all alternatives
case Dog(age=n) | Cat(name=n):     # error if age:Int32 vs name:str (type mismatch)
```

---

## Codegen Strategy

### Subject evaluation

The subject expression is evaluated once and bound to a local:

```cpp
// match expr:
auto& __match_subject = expr;  // lvalue subject: bind by reference
auto __match_subject = expr;   // rvalue/temporary: bind by value
```

For union subjects, the reference avoids copying the variant.

**TODO (optimization):** When the subject is a simple name (e.g. `match s:`
where `s` is already a local/param), skip the `__match_subject` binding and
use `s` directly -- consistent with isinstance codegen which operates on the
original variable.

**TODO (optimization):** Pattern bindings for `str` fields (e.g.
`case Dog(name=n):`) currently pre-declare as `std::string` (copy). When the
binding is read-only (not reassigned in the body), it could be
`std::string_view` for zero-copy. Requires integrating pattern bindings with
the local str deduction system (`local_deduction.py`).

### Union subjects

Generate a `switch` on the variant index. Since TPy controls canonical
member ordering (alphabetical, NoneType last), the index-to-type mapping
is deterministic. This is simpler and more efficient than `std::visit`
(avoids same-return-type requirement and lambda overhead).

```python
# Python
match animal:
    case Dog(name=n):
        print("dog:", n)
    case Cat(name=n):
        print("cat:", n)
    case _:
        print("other")
```

```cpp
// C++ -- variant<Cat, Dog> (alphabetical: Cat=0, Dog=1)
auto& __match_subject = animal;
switch (__match_subject.index()) {
    case 1: {
        auto& __dog = std::get<1>(__match_subject);
        auto& n = __dog.name;
        std::cout << "dog: " << n << "\n";
        break;
    }
    case 0: {
        auto& __cat = std::get<0>(__match_subject);
        auto& n = __cat.name;
        std::cout << "cat: " << n << "\n";
        break;
    }
    default: {
        std::cout << "other" << "\n";
        break;
    }
}
```

Wildcard/capture maps to `default:`. Each case arm gets a `break`.

A literal field sub-pattern (`case Dog(legs=4):`) is a *conditional* arm: it
matches only when the variant type *and* the field value match. Such an arm
routes to the guarded switch path, which emits the field comparison as an
`if (__case.legs == 4)` inside the `case` block (same `_record_field_conditions`
machinery as concrete-record subjects) and falls through to a later `Dog()` /
`_` arm when it fails. A conditional arm therefore does not count toward
exhaustiveness -- the variant is still reported uncovered.

### Optional subjects

For `T | None` using `std::optional<T>`:

```python
match maybe_val:
    case None:
        print("nothing")
    case int() as v:
        print(v)
```

```cpp
auto& __match_subject = maybe_val;
if (!__match_subject.has_value()) {
    std::cout << "nothing" << "\n";
} else {
    auto& v = *__match_subject;
    std::cout << v << "\n";
}
```

For `T | None` using `std::variant<T, std::monostate>` (union with
NoneType): same pattern as union subjects, with `std::monostate` for
the None case.

### Enum subjects

```python
match color:
    case Color.RED:
        print("red")
    case Color.GREEN:
        print("green")
    case _:
        print("other")
```

```cpp
auto __match_subject = color;
switch (__match_subject) {
    case Color::RED: {
        std::cout << "red" << "\n";
        break;
    }
    case Color::GREEN: {
        std::cout << "green" << "\n";
        break;
    }
    default: {
        std::cout << "other" << "\n";
        break;
    }
}
```

### Primitive subjects (int, str, bool)

```python
match command:
    case "quit":
        handle_quit()
    case "help":
        handle_help()
    case other:
        print(f"Unknown: {other}")
```

```cpp
auto __match_subject = command;
if (__match_subject == "quit") {
    handle_quit();
} else if (__match_subject == "help") {
    handle_help();
} else {
    auto& other = __match_subject;
    std::cout << ("Unknown: " + std::string(other)) << "\n";
}
```

### Concrete record subjects (field-value matching)

```python
match point:
    case Point(x=0, y=0):
        print("origin")
    case Point(x=x, y=0):
        print(f"on x-axis at {x}")
    case Point(x=x, y=y):
        print(f"at ({x}, {y})")
```

```cpp
auto& __match_subject = point;
if (__match_subject.x == 0 && __match_subject.y == 0) {
    std::cout << "origin" << "\n";
} else if (__match_subject.y == 0) {
    auto& x = __match_subject.x;
    std::cout << ("on x-axis at " + ...) << "\n";
} else {
    auto& x = __match_subject.x;
    auto& y = __match_subject.y;
    std::cout << ("at (" + ... + ")") << "\n";
}
```

Class patterns with no literal sub-patterns (all captures/wildcards) are
always-matching and act as a default arm. With guards, record matches use
standalone `if` blocks with `goto` (same approach as guarded unions).

### Optional subjects

For value-type optionals (`std::optional<T>`):

```python
match maybe_val:
    case None:
        print("nothing")
    case 42:
        print("answer")
    case _:
        print("other")
```

```cpp
auto& __match_subject = maybe_val;
if (!__match_subject.has_value()) {
    std::cout << "nothing" << "\n";
} else if (__match_subject.has_value() && (*__match_subject) == 42) {
    std::cout << "answer" << "\n";
} else {
    std::cout << "other" << "\n";
}
```

For pointer-repr optionals (`T*`, where T is a record):

```python
match maybe_point:
    case None:
        print("nothing")
    case Point(x=0, y=0):
        print("origin")
```

```cpp
auto& __match_subject = maybe_point;
if (__match_subject == nullptr) {
    // None arm
} else if (__match_subject != nullptr && (*__match_subject).x == 0 && (*__match_subject).y == 0) {
    // origin arm
}
```

### Polymorphic (`@dynamic` protocol / polymorphic class) subjects

When the subject carries a `@dynamic`-rooted C++ vtable -- a bare `Pet`, a
`Ptr[Pet]`, or the deref view of an owning wrapper (`Box[Pet]` / `Rc[Pet]`) --
match dispatches by runtime type, the statement-level sibling of `isinstance`
subclass narrowing. It reuses the same engine: the subject-source detection
(`polymorphic_source_inner` / deref-dispatch peel), the cast-argument helpers
(`polymorphic_cast_arg` / `deref_view_cast_arg`), and the cast-rhs builder
(`ProtocolGenerator.dynamic_narrow_cast_rhs`, which picks `dynamic_cast` for an
inheritance conformer vs `tpy::dyn_adapter_cast` for a structural conformer).

```python
def describe(p: Pet) -> str:       # Pet is a @dynamic protocol
    match p:
        case Dog(name=n):
            return "dog:" + n + ":" + p.speak()
        case Cat():
            return "cat:" + p.speak()
        case _:
            return "?"
```

```cpp
std::string describe(Pet& p) {
    if (Dog* __mpoly_0 = dynamic_cast<Dog*>(&p)) {
        Dog& __case_0 = *__mpoly_0;
        auto& n = __case_0.name;
        // `p.speak()` narrows to `__case_0.speak()` inside the arm
        return ...;
    } else if (Cat* __mpoly_1 = dynamic_cast<Cat*>(&p)) {
        Cat& __case_1 = *__mpoly_1;
        return ...;
    } else {
        return "?";
    }
}
```

Each class-pattern arm casts the subject's payload pointer to the matched
subclass; on success it binds a `[const] Sub& __case_i` reference and narrows
the subject (`narrowed_vars`) so member access in the arm resolves against the
subclass. The unconditional (no-guard) form is an `if`/`else if` chain (a failed
cast naturally falls to the next arm); the guarded form uses standalone `if`
blocks with a `goto __match_end` label, like guarded unions/records, so a failed
guard falls through to the next arm. A `const` borrow source produces
`dynamic_cast<const Sub*>` and a `const Sub&` binding. Or-patterns lower to an
`||` of `dynamic_cast != nullptr` tests (type-test only; no field bindings).

A polymorphic subject ranges over an open subclass set, so sema treats the
match as exhaustive only when an unconditional catch-all is present -- a
wildcard/capture arm, or a class pattern naming the subject's own concrete root
(whose cast always succeeds). Otherwise it warns (the match simply falls through
when no arm matches, matching CPython). A later arm whose type is a
subclass-or-equal of an earlier arm's type is rejected as unreachable.

When the subject's static type is a *concrete* class that is itself
`@dynamic`-rooted (both record-like and polymorphic), polymorphic dispatch
takes precedence over record field-value matching -- consistent with
`isinstance`, which dispatches by runtime type on such a value. The regression
pin is `tests/cases/match/poly_concrete` (a `case Dog():` subclass arm on an
`Animal` subject compiles only under polymorphic precedence). Field-value
matching on such a class is therefore expressed with a guard, not a record
arm.

#### As-built gaps

- A literal field sub-pattern (`case Dog(legs=4):`) in a polymorphic arm
  compares the field after the `dynamic_cast`: the arm routes to the guarded
  path, which emits `if (__case.legs == 4)` (via `_record_field_conditions`,
  shared with union / record subjects) and falls through to a later `Dog()` /
  `_` arm when it fails. A *named-constant* comparison (`case Dog(size=Big):`)
  is not yet emitted -- use a guard (`case Dog() if d.size == Big:`).
- The subject may be an arbitrary expression -- a bare name, subscript, field,
  rvalue call, or wrapper deref view. It is evaluated once into
  `__match_subject` (`auto&` for an lvalue so a `case C() as v: v.f = ...` arm
  writes through; `auto` for an rvalue so a temporary outlives the deref-view
  cast), and the dynamic_cast chain runs off that binding. Only a bare-name
  subject narrows the subject for in-arm reads (`p.speak()` resolves to the
  matched subclass); an expression subject reaches the narrowed value via
  `as` / a capture. This is a deliberate divergence from `isinstance`, which
  stays bare-name-only: `match` re-evaluates the subject (CPython semantics)
  rather than narrowing a name in place, so it needs no dotted-path key.
- `Optional[Pet]` is not routed here -- polymorphic dispatch on an optional
  additionally needs a `None` arm (a separate, unimplemented path); it stays
  rejected by the existing Optional handling.
- A `yield` / `await` inside a polymorphic-match arm is rejected (the
  dynamic_cast chain is not threaded through the resumable frame's arm routing).
- Or-pattern alternatives bind no variables (a binding would have no single
  well-typed value across distinct subclasses).

### Guards

Guards add a condition that must be true for the case to match. The key
constraint: a failed guard must fall through to the next case.

**Union subjects with guards**: uses standalone `if` blocks with
`goto`-based forward jumps to skip remaining arms after a match:

```cpp
// case Dog(name=n) if n == "Rex":  ->  case Dog():
if (std::holds_alternative<Dog>(__match_subject)) {
    auto& __case_0 = std::get<Dog>(__match_subject);
    auto& n = __case_0.name;
    if (n == "Rex") {
        // guarded body
        goto __match_end_0;
    }
}
if (std::holds_alternative<Dog>(__match_subject)) {
    // unguarded fallback for Dog
    goto __match_end_0;
}
__match_end_0:;
```

The `goto` approach avoids `if/elif` chains (which prevent fallthrough
from a failed guard to a later case for the same type) and avoids
`do/while(false)+break` (which would capture `break`/`continue` from
user code inside the match body).

**Enum/primitive subjects with guards**: still uses `switch`, with
if/else guard chains inside each case arm. Same-value cases with
different guards are merged. Failed guards use `goto` to the default
arm:

```cpp
// case Color.Green if x: / case Color.Green if y: / case Color.Green: / case _:
switch (__match_subject) {
case Color::Green: {
    if (x) {
        return "green+x";
    } else if (y) {
        return "green+y";
    } else {
        return "green";
    }
    break;
}
default: {
    return "other";
    break;
}
}

// case Color.Green if allow: / case _:  (no unguarded same-value fallback)
switch (__match_subject) {
case Color::Green: {
    if (allow) {
        return "guarded-green";
    }
    goto __match_default_1;
    break;
}
default: __match_default_1: {
    return "other";
    break;
}
}
```

**Record subjects with guards**: uses standalone `if` blocks with
`goto` (same approach as unions). Field conditions are checked first,
then captures bound, then guard evaluated inside a nested `if`:

```cpp
// case Point(x=x) if x > 0:
if (true) {
    auto& x = __match_subject.x;
    if (x > 0) {
        // body
        goto __match_end_1;
    }
}
// case _:
{ ... }
__match_end_1:;
```

**String/float/bool and Optional subjects with guards**: same
standalone-`if` + `goto` shape as records/unions. Bindings are emitted
before the guard (a guard may read its own captures), and a failed guard
falls out of the arm block to the next arm -- including a later
duplicate-literal arm:

```cpp
// case "hello" as v if formal(v):
if (__match_subject_1 == "hello") {
    auto& v = __match_subject_1;
    if (formal(v)) { ...; goto __match_end_2; }
}
// next arm...
__match_end_2:;
```

### Or-patterns

Or-patterns use `switch` with case fallthrough for union, enum, and
primitive subjects. String/float subjects fall back to if/elif with `||`.

Without bindings -- case fallthrough:

```python
case Dog() | Cat():
    print("pet")
```

```cpp
// Union: switch on .index()
case 1:   // Cat
case 2:   // Dog
{
    std::cout << "pet" << "\n";
    break;
}

// Enum: switch on value
case Color::Red:
case Color::Blue:
{
    // body
    break;
}

// Primitive: switch on value
case 1:
case 2:
case 3:
{
    // body
    break;
}
```

With bindings -- body duplication per alternative inside switch:

```python
case Dog(name=n) | Cat(name=n):
    print(n)
```

```cpp
case 2: {  // Dog
    auto& __case_0_0 = std::get<2>(__match_subject);
    auto& n = __case_0_0.name;
    std::cout << n << "\n";
    break;
}
case 1: {  // Cat
    auto& __case_0_1 = std::get<1>(__match_subject);
    auto& n = __case_0_1.name;
    std::cout << n << "\n";
    break;
}
```

Wildcard alternatives (`case Dog() | _:`) emit `default:` in the switch.

Body duplication is acceptable since case bodies are typically small.
Lambda-based body sharing is a future optimization.

---

## Interaction with Existing Features

### Union narrowing

Match/case on union types reuses the same codegen primitives as isinstance
narrowing: `std::holds_alternative<T>`, `std::get<T>`, narrowed variable
aliases (`auto& __name = std::get<T>(subject)`). The `NarrowingTracker`
infrastructure can be leveraged for type facts within case bodies.

### Enum comparison

Enum value patterns (`case Color.RED:`) reuse the existing enum `==`
codegen. No new primitives needed.

### Optional narrowing

`case None:` on Optional subjects reuses the existing `is None` /
`has_value()` codegen patterns.

### Type aliases

`Shape = Circle | Rect` -- matching on `Shape` works because the alias
resolves to the underlying `UnionType`. Exhaustiveness checking uses the
resolved members.

### `@dataclass`

Dataclass records have natural `__match_args__` from field declaration
order. Positional class patterns work out of the box.

---

## Semantic Differences from CPython

Minor differences where TPy's compiled model diverges:

| Aspect | CPython | TPy | Impact |
|--------|---------|-----|--------|
| Singleton matching | `is` identity check | `==` comparison | No observable difference for None/True/False in C++ |
| `__match_args__` | Only `@dataclass` auto-generates | All records auto-generate from field order | Strictly more permissive; valid CPython code still works |
| Sequence patterns | Runtime protocol check | Phase 2 (not yet) | Missing feature, not a semantic difference |
| Mapping patterns | Runtime protocol check | Phase 2 (not yet) | Missing feature, not a semantic difference |
| Soft keyword | `match`/`case` usable as variable names | Same (Python ast handles this) | No difference |
| Polymorphic subject expression | Any expression | Any expression (bound once into `__match_subject`); `isinstance` still requires a bare name | Match: no difference. Deliberate divergence from `isinstance`, which stays bare-name-only |
| Shadowed polymorphic arm | Silently allowed (dead arm) | Compile error (`unreachable case`) | Stricter: rejects an arm CPython would accept-but-never-run |
| Non-conformer polymorphic arm | Silently allowed (never matches) | Compile error | Stricter: a `case T()` whose `T` can't match the subject is rejected, not dead code |

All three polymorphic-dispatch rejections emit explicit diagnostics, so no silent
divergence -- they tighten valid-but-buggy CPython into a compile error.

---

## Error Diagnostics

### Compile-time errors

| Condition | Message |
|-----------|---------|
| Class pattern on non-member of union | `'Triangle' is not a member of union 'Circle \| Rect'` |
| Duplicate case for same type | `duplicate case for 'Circle' in match statement` |
| Incompatible literal type | `cannot match 'str' literal against 'Int32' subject` |
| Or-pattern variable mismatch | `variable 'n' not bound in all alternatives of or-pattern` |
| Or-pattern type mismatch | `variable 'n' has type 'Int32' in first alternative but 'str' in second` |
| Too many positional args | `'Point' accepts 2 positional patterns but 3 were given` |
| Unknown field in keyword pattern | `'Circle' has no field 'width'` |
| Unreachable case after wildcard | `unreachable case after wildcard pattern` |
| Value-only arm after an Optional class catch-all | `unreachable case: every non-None value is already matched by an earlier arm` |
| Optional capture re-typing an existing variable | `match capture 'x' binds the full Optional subject ('Int32 \| None') but 'x' already has type 'Int32'; ...` |
| `as` binding on `case None:` | `'as' binding not allowed on 'case None:'` |

### Compile-time warnings

| Condition | Message |
|-----------|---------|
| Non-exhaustive union match | `non-exhaustive match on '...'; missing: Bird` |
| Non-exhaustive enum match | `non-exhaustive match on '...'; missing: Color.BLUE` |
| Non-exhaustive Optional match | `non-exhaustive match on '...'; missing: None, Point` (each uncovered side reported) |
| Subject storage mutated under live bindings | `'h.pet' is mutated in this arm while pattern bindings borrow its storage; the bindings dangle (undefined behavior). ...` (field/element subjects; arm body or guard; syntactic -- aliases evade, see BUGS.md) |
| Non-enumerable scalar subject without a catch-all | `non-exhaustive match on '...'; no unconditional catch-all arm` |

---

## Test Plan

Tests go in `tests/cases/match/` (new directory).

| Test | What it covers |
|------|---------------|
| `union_basic` | match on `A \| B \| C` with class patterns |
| `union_field_binding` | `case Circle(radius=r):` keyword binding |
| `union_positional` | `case Point(x, y):` positional binding |
| `union_wildcard` | `case _:` default arm |
| `union_capture` | `case x:` capture binding |
| `union_as_pattern` | `case Dog() as d:` pattern + name |
| `enum_basic` | match on enum with value patterns |
| `optional_basic` | match on `T \| None` with None pattern |
| `literal_int` | match on `Int32` with literal patterns |
| `literal_str` | match on `str` with literal patterns |
| `or_pattern` | `case Dog() \| Cat():` |
| `or_pattern_bindings` | `case Dog(name=n) \| Cat(name=n):` |
| `guard_basic` | `case X() if cond:` |
| `guard_fallthrough` | guard fails, next case matches |
| `field_value_match` | `case Point(x=0, y=0):` on concrete record |
| `nested_match` | match inside match (legal Python) |
| `error_not_member` | class pattern for non-union-member |
| `error_or_var_mismatch` | or-pattern with inconsistent bindings |
| `error_unreachable` | case after wildcard |
| `error_incompatible_literal` | wrong literal type for subject |
| `exhaustive_union` | exhaustive match -- no warning |
| `nonexhaustive_union` | missing member -- warning emitted |
| `nonexhaustive_enum` | missing enum member -- warning emitted |

All tests should also pass the CPython compatibility phase of `test_case` (unless marked with `no_cpython.txt`).
