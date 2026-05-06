# Dynamic Attributes Design

User-defined `__getattr__`, `__setattr__`, `__delattr__` dunders. Roadmap entry: D16
(`docs/FEATURE_ROADMAP.md`).

## Progress

D16 v1 + v1.5 (phases 7-9) are shipped. `AttributeError` is a catchable
throw-tier exception. `hasattr`, 3-arg `getattr`, and dynamic-name
2-arg builtins all work. v2 features (intercept-all `__setattr__`,
`__getattribute__`, `__dir__`, etc.) are listed under Future Extensions
and remain driver-dependent.

### v1 -- core attribute routing

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | `__getattr__` recognition + read-side routing for `obj.foo` (literal-name attribute access only) + literal-name `getattr(obj, "foo")` builtin restricted to dynamic-fallback (errors on declared-member names) | Done |
| 2 | `__setattr__` recognition + write-side routing for `obj.foo = v` + literal-name `setattr(obj, "foo", v)` builtin (dynamic-fallback only) | Done |
| 3 | `__delattr__` + `del obj.foo` + literal-name `delattr(obj, "foo")` builtin (dynamic-fallback only). Includes parser plumbing: lift the `del` attribute `ParseError` at `parse/parser.py:3039`, add a TPy AST node for attribute-delete, sema + codegen paths | Done |
| 4 | Mutation propagation, `@readonly` enforcement, signature validation, async/generator/decorator rejection rules | Done |
| 5 | Single-class inheritance/MRO via shared lookup helper, narrowing-via-local docs/tests | Done |
| 6 | Diagnostics polish, `docs/LANGUAGE_FEATURES.md` update, regression cases | Done |

### v1.5 -- gated extensions

| Phase | Description | Status |
|-------|-------------|--------|
| 7 | `hasattr(obj, name)` for static + dyn-readable classes | Done. Declared members fold to compile-time `True`; classes without `__getattr__` fold to `False` (literal-name only); dyn-readable classes emit a try/catch stmt-expr that yields `false` on `AttributeError`, `true` otherwise |
| 8 | 3-arg `getattr(obj, name, default)` with `AttributeError` catch | Done. Stmt-expr block yields the dunder's result, or `default` (coerced to `T`) on `AttributeError` |
| 9 | Dynamic-name 2-arg builtins: `getattr(obj, name_var)`, `setattr(obj, name_var, v)`, `delattr(obj, name_var)`, `hasattr(obj, name_var)` | Done -- **Option A: route-all-to-dunder**. When the name is not a literal, the builtin routes unconditionally to the dunder (no static prefix dispatch over declared members). CPython divergence: `getattr(obj, declared_field_name_var)` calls `__getattr__` rather than reading the declared field -- documented in divergence #8 |

### Known Limitations (v1)

| Limitation | Notes |
|------------|-------|
| `__setattr__` is fallback-only | Declared-field writes bypass the dunder. Intentional divergence from CPython, see Divergences |
| No `__getattribute__` | The every-access hook is out of scope; use `__getattr__` for fallback or `@property` for per-attribute interception |
| No auto `__dict__` / `vars(obj)` | Classes don't grow a per-instance bag; user backs storage themselves (e.g. a `dict` field) |
| No overloaded dunders | Each dunder has a single signature |
| Not on `@native` records | Native record shape is owned by hand-written C++; mixing dyn-attr is out of scope |
| Single-name dispatch only | The dunder receives the requested name as `str`; no compile-time multi-name specialization |
| Dynamic-name builtins always route to the dunder | `getattr(obj, name_var)` (and friends) call `__getattr__` even when `name_var` matches a declared member at runtime. CPython would read the declared field instead. Use direct `obj.field` access for declared members. See divergence #8 |
| Builtin `getattr` / `setattr` / `delattr` are dynamic-fallback only | `getattr(obj, "declared_field")` is a compile error in v1 (use `obj.declared_field` instead). Avoids the bound-method / value-category snake pit until method references (TODO.md:101) land |
| `__getattr__` returns must be non-borrowed | Reference / view returns from `__getattr__` deferred (borrow-checker interaction non-trivial). Owned and value-type returns are fine |
| Async / generator dunders rejected | Same rejection list as `@staticmethod` / `@classmethod` / `@property` / `@overload` |
| Generic-receiver routing requires concrete class | `def f[T](x: T): x.foo` with `T` unresolved gets static-only behavior, no dunder fallback |
| Inherited-generic dyn-attr not required for v1 | `class Box[T](Parent): ...` inheriting a dunder from a generic parent is allowed if the shared MRO helper handles it naturally, but is not part of the v1 success criteria |

### Future Extensions and Design Space

Status legend: **v2** = direct follow-up to v1, tracked, no plan; **possible** =
fits TPy's model, no design yet; **not planned** = anti-philosophy or fights
static codegen, listed only to document the boundary.

#### Direct extensions of v1 (same machinery)

| Feature | Status | Notes |
|---------|--------|-------|
| Intercept-all `__setattr__` | v2 | Opt-in (`@intercept_all` decorator or similar) so declared writes also route through the dunder. Will need an escape hatch for recursion (CPython uses `object.__setattr__`; TPy currently doesn't recognize it -- would have to be designed alongside). Driver: validating/logging proxies that filter every write |
| Intercept-all `__delattr__` | v2 | Symmetric with above, but declared-field deletes are illegal anyway, so usefulness is narrow |
| `__getattribute__` | v2 | Every-access hook (called *before* normal lookup, not just on miss). Turns every `obj.x` into a virtual call. Defer until a real driver appears |
| `__dir__` | v2 | Customize the result of `dir(obj)`. Cosmetic, but cheap once `dir(obj)` exists |
| Routing through `@dynamic` protocols | v2 | `Adapter::call("bar", ...)` falls through to `__getattr__` when underlying class is dyn-readable. Opens dyn-attr proxies to dyn-protocol consumers |
| `__missing__` on dict subclasses | v2 | Different surface (dict subclasses only) but same dynamic-fallback flavor. Likely lives with a dict-subclass effort, not D16 |

#### Hybrid static-dynamic (typed dyn-attrs)

These let users get dyn-attr ergonomics with static typing, where the schema is
known. They reduce the "everything is `Any`, narrow before use" friction.

| Feature | Status | Notes |
|---------|--------|-------|
| TypedNamespace | possible | Per-attr-name static types -- TypedDict but for attribute access. `class N(TypedNamespace): host: str; port: Int32` makes `n.host` typed `str` without narrowing. Big design effort; replaces heterogeneous-`Any` proxies when schema is known |
| Compile-time attribute schema | possible | Lighter than TypedNamespace: a `__attrs__ = {"host": str, "port": Int32}` declaration consulted when typing dyn-attr access. Stepping stone to TypedNamespace |
| `__getattr__` body inlining | possible | When `__getattr__` body is a simple lookup (`return self._data[name]`) and storage has known schema, inline dispatch at compile time. Optimization, not a user-facing feature |
| Macro-based per-name typing | done (in spirit) | Already exists via `@class_macro` / `@builder_macro` -- argparse, `@dataclass`, JSON `@model` synthesize typed records. The compile-time route to "typed dynamic attrs" -- D16 is the runtime route |

#### Convenience / stdlib (built on v1, not new compiler primitives)

| Feature | Status | Notes |
|---------|--------|-------|
| `types.SimpleNamespace` | v2 | Implementable as a small TPy class with `__getattr__`/`__setattr__` over a `dict[str, Any]` field once D16 lands. No compiler change needed |
| `Namespace` marker base class | possible | Convenience: inherit `__getattr__`/`__setattr__` from a stdlib base instead of writing them. Sugar over user-written dunders |
| Auto per-instance `__dict__` | v2 | Compiler-allocated bag for classes inheriting from `Namespace` or marked `@dynamic_attrs`. Couples with `SimpleNamespace`. Real cost; defer to driver |
| `obj.__dict__` exposure | v2 | Requires runtime per-instance dict; couples with auto-`__dict__` |
| `vars(obj)` | v2 | Alias for `obj.__dict__`; same dependency |
| `inspect.getmembers` compat | possible | Test/tooling compatibility; depends on `dir`/`__dict__` exposure |

#### Adjacent Python dynamic features (related design space, separate efforts)

| Feature | Status | Notes |
|---------|--------|-------|
| `__init_subclass__` | possible | Hook fired when a class is subclassed. Metaclass-lite; useful for registries/plugin systems. Independent of dyn-attrs but in the same dynamic-class neighborhood |
| `__set_name__` | possible | Descriptor naming hook. Coupled to descriptor protocol |
| Module-level `__getattr__` (PEP 562) | possible | Lazy module attributes. Smaller surface than class-level dyn-attrs; could land independently |
| Full descriptor protocol (`__get__`/`__set__`/`__delete__`) | not planned | Underlies `@property`/`@classmethod`/`@staticmethod` in CPython, but TPy provides these as compiler-recognized decorators directly. User-written descriptors don't fit static dispatch |
| Metaclasses | not planned | Fights static codegen. `@class_macro` covers the ergonomic use cases |
| `__class__` reassignment | not planned | Runtime class swap is fundamentally incompatible with static layout |
| Runtime class creation (`type(name, bases, dict)`, `dataclasses.make_dataclass`) | not planned | Dynamic class objects don't fit the static record model. The macro system is the compile-time analogue |
| Monkey-patching (`setattr(cls, "method", fn)`) | not planned | Same problem as runtime class creation |

#### Serialization / introspection / interaction with other systems

| Feature | Status | Notes |
|---------|--------|-------|
| `__getstate__` / `__setstate__` | possible | Pickle protocol. General feature, not D16-specific, but a pickled dyn-attr class needs round-tripping for its user-managed storage |
| `copy.copy` / `copy.deepcopy` | possible | Standard semantics; a class with `__getattr__` over a backing field copies the field normally, dyn-attrs follow |
| `__eq__` / `__hash__` on `Namespace`-style classes | possible | If a stdlib `Namespace` ships, comparing the underlying dict store is the obvious behavior |
| Compatibility with `@native` records | not planned | Native records are owned by hand-written C++; dunders defined on the Python side don't fit. Use a wrapper class instead |
| Compatibility with `@nocopy` / `Own[T]` storage in dyn-attrs | depends on Any v2 | Storing move-only types via `__setattr__(self, name, value: Own[T])` works directly (Own param). Storing them via an `Any`-backed bag is blocked on Any v2 (move-only contents support) |

#### Things D16 deliberately doesn't try to solve

| Non-goal | Why |
|----------|-----|
| Per-name return-type specialization on a single `__getattr__` | A function has one return type. Per-name typing belongs to TypedNamespace or macros, not the dunder |
| Bypass-the-dunder-from-outside | CPython has `object.__getattribute__(obj, ...)`. TPy users can call the dunder directly (`obj.__getattr__("foo")`); no separate primitive |
| Replacing `@property` | `@property` already exists for per-attribute interception with static typing. `__getattr__` is the catch-all fallback |
| Replacing `@dataclass` / record fields | Static fields stay zero-cost. D16 is the escape hatch, not the new default |

---

## Overview

Two motivating shapes:

```python
# Heterogeneous proxy: returns Any, caller narrows
class Config:
    _data: dict[str, Any]

    def __init__(self, data: dict[str, Any]) -> None:
        # Declared writes always bypass __setattr__ under TPy.
        self._data = data

    def __getattr__(self, name: str) -> Any:
        return self._data[name]
    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

cfg = Config({...})
host = cast(str, cfg.host)        # narrow on use
cfg.port = Int32(8080)            # writes to dict
```

```python
# Homogeneous proxy: concrete return type, no narrowing needed
class Headers:
    _store: dict[str, str]

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    def __getattr__(self, name: str) -> str:
        return self._store.get(name, "")

h = Headers(...)
ct = h.content_type             # typed as str directly
```

The compiler treats the dunders as ordinary methods and reroutes attribute
access / assignment / deletion to them when the name is not statically
resolvable.

CPython source-compatibility caveat: a class that defines `__setattr__` and
also writes declared fields in `__init__` (the shape above) recurses under
CPython, because CPython's `__setattr__` is intercept-all. The standard
CPython escape hatch is `object.__setattr__(self, "field", value)`. TPy does
NOT recognize that form -- if a test must verify both backends, it has to
either avoid defining `__setattr__` or skip the CPython phase via
`no_cpython.txt`. The shipped tests under `tests/cases/dynamic_attrs/` that
define `__setattr__` are TPy-only for that reason.

## Goals

1. Let any user class declare `__getattr__` / `__setattr__` / `__delattr__` and have
   them participate in attribute access / assignment / deletion.
2. Zero cost on classes that don't declare any dunder. Codegen for static records
   stays bit-identical to today.
3. Concrete return types supported; `Any` works for heterogeneous proxies, but
   homogeneous proxies (e.g. `-> str`) skip the narrowing step entirely.
4. Faithful CPython compatibility for the *fallback* semantics of these dunders
   (called only when normal lookup fails) so test programs can run under both
   backends.

## Non-Goals

1. Intercept-all `__setattr__` -- declared-field writes bypass the dunder.
2. Per-instance `__dict__` allocated by the compiler.
3. `__getattribute__` (every-access hook).
4. `SimpleNamespace` as a compiler primitive.
5. Heterogeneous `__getattr__` returning *different statically-known types* per
   name. The signature is one return type for all undeclared names; per-name
   typing belongs to a hypothetical TypedNamespace feature, not D16.

---

## Trigger Rules

A class is *dyn-readable* iff it declares (or inherits via MRO) a method named
`__getattr__`. Independently, it is *dyn-writable* iff it declares `__setattr__`,
and *dyn-deletable* iff it declares `__delattr__`. The three are independent --
`__getattr__` alone is a useful read-only proxy.

Required signatures (validated in sema, exact match):

```python
def __getattr__(self, name: str) -> T: ...        # T per the return-type rule below
def __setattr__(self, name: str, value: V) -> None: ...   # V arbitrary; return must be None
def __delattr__(self, name: str) -> None: ...     # return must be None
```

- Exact parameter counts: `__getattr__` takes `(self, name)`, `__setattr__`
  takes `(self, name, value)`, `__delattr__` takes `(self, name)`. No defaults,
  no `*args`, no `**kwargs`, no method-level type parameters
  (`def __getattr__[U](...)`), no positional-only / keyword-only markers in v1.
- `name` parameter must be `str` in v1. (`StrView` is a possible future
  optimization to avoid the per-access copy of literal names; `str` keeps
  the v1 surface uniform.)
- `__setattr__` and `__delattr__` must declare `-> None`.
- Must be regular methods -- not `@staticmethod`, `@classmethod`, `@property`,
  `@overload`, `async def`, generator (`yield` in body), or `@error_return`.
  (Mixing `@error_return` with attribute-access routing would surface the
  `expected<T, E>` machinery into every undeclared `obj.foo` site; not v1.)
- Standard `@readonly` rules apply (`__getattr__` typically `@readonly`,
  `__setattr__` / `__delattr__` mutating).
- `@native` records cannot declare these dunders (out of scope, see limitations).
- `__getattr__` return type in v1 must be one of: a value type (primitives,
  `Char`, value-type user records, tuples), `Any`, or an explicit `Own[T]`
  ownership transfer. Bare reference types are rejected -- including
  user records / `list` / `dict` / `set` / `bytes` / `bytearray` (which
  default to reference-return at the boundary), and views (`Span[T]`,
  `Ptr[T]`, `Ref[T]`, `StrView`, `BytesView`). For non-value owned returns,
  use `Own[T]` explicitly. This is stricter than "no Span/Ptr" and matches
  how `__getattr__` actually behaves: the dunder body computes a result, and
  there is no place to borrow it from.

---

## Lookup Semantics

### Read: `obj.foo`

1. If `foo` resolves statically (declared field, method, property, class-level
   constant) -> existing static behavior. Dunder NOT called.
2. Else if class is dyn-readable -> emit `obj.__getattr__("foo")`. Result types
   as the dunder's declared return type `T`.
3. Else -> existing "no field" error.

Receiver type must resolve to a concrete class for the dyn-readable check;
unresolved type vars and protocol-typed receivers get static-only behavior.

### Read: `getattr(obj, "foo")` (v1, fallback-only)

In v1 the builtin only handles the **dynamic-fallback** case:

1. If `"foo"` matches a declared member of `obj`'s class -> compile error
   ("use `obj.foo`; `getattr` for declared members is not yet supported"). This
   defers the bound-method / value-category questions to a v2 unification with
   method references (`TODO.md:101`).
2. Else if class is dyn-readable -> emit `obj.__getattr__("foo")`. Same routing
   as `obj.foo`, same result type `T`.
3. Else -> existing "no field" error.

### Read: `getattr(obj, "foo", default)` (v1.5 phase 8, literal name only)

Implemented for literal names. The 3-arg form catches `AttributeError`
from the dunder and substitutes `default`. Type rule: `default` must be
coercible to `T` (the dunder's declared return type); result is typed as
`T`. (Users wanting `T | D` widening declare `T = Optional[X]`
explicitly.)

1. If `"foo"` matches a declared member -> compile error (same v1 rule).
2. Else if class is dyn-readable -> emit a stmt-expr block that calls
   `__getattr__("foo")` in a try block and yields `default` (coerced to
   `T`) in the catch.
3. Else -> existing "no field" error.

Dynamic-name `getattr(obj, name_var, ...)` is shipped (phase 9, Option A):
the builtin routes unconditionally to `__getattr__` -- no static prefix
dispatch over declared members. The receiver must be dyn-readable.

### Write: `obj.foo = v`

1. If `foo` is a declared field -> existing static assignment. Dunder NOT called.
2. Else if class is dyn-writable -> emit `obj.__setattr__("foo", v)`. Value `v`
   must be coercible to `V`. AttributeError raised in the body propagates
   as a normal C++ throw.
3. Else -> existing "no field" error.

### Write: `setattr(obj, "foo", v)` (v1, fallback-only)

Same v1 restriction as `getattr`: literal name only.

1. If `"foo"` matches any declared member of `obj`'s class (field, property,
   method, class constant) -> compile error ("use `obj.foo = v`; `setattr` for
   declared members is not yet supported"). Methods and class constants are
   never assignable in TPy regardless; they error here too for symmetry with
   the read-side rule.
2. Else if class is dyn-writable -> emit `obj.__setattr__("foo", v)`.
3. Else -> error.

Dynamic-name `setattr(obj, name_var, v)` is shipped (phase 9, Option A):
routes to `__setattr__` unconditionally. The receiver must be dyn-writable.

### Delete: `del obj.foo`

Requires parser plumbing in v1: lift the `ParseError` at
`parse/parser.py:3039`, add a TPy AST node for attribute-delete, route through
sema and codegen.

1. If `foo` is a declared field -> error (declared fields are not deletable;
   record layout is fixed). Dunder NOT called.
2. Else if class is dyn-deletable -> emit `obj.__delattr__("foo")`.
3. Else -> error.

### Delete: `delattr(obj, "foo")` (v1, fallback-only)

1. If `"foo"` matches any declared member -> compile error (declared fields
   are not deletable; declared methods/properties/constants are not deletable
   either).
2. Else if class is dyn-deletable -> emit `obj.__delattr__("foo")`.
3. Else -> error.

Dynamic-name `delattr(obj, name_var)` is shipped (phase 9, Option A):
routes to `__delattr__` unconditionally. The receiver must be dyn-deletable.

### `hasattr(obj, "foo")` (v1.5 phase 7, literal name only)

Implemented for literal names. Rules:

- Literal name matches a declared field / property / method / class
  constant -> compile-time `True` (folded to `TpyBoolLiteral` in sema).
- Literal name not declared, class has no `__getattr__` -> compile-time
  `False`.
- Literal name not declared, class is dyn-readable -> emit a stmt-expr
  block that calls `__getattr__("foo")` in a try block and yields
  `false` on `AttributeError`, `true` otherwise.
- Catches only `AttributeError`; other exceptions propagate (matches
  Python 3).

Dynamic-name `hasattr(obj, name_var)` is shipped (phase 9, Option A):
the builtin routes unconditionally to `__getattr__` and reports True
if the dunder returns, False on AttributeError. The receiver must be
dyn-readable.

---

## Return / Value Types

`__getattr__ -> T`: in v1, `T` must be one of:

- a value type. Per TPy's terminology, that includes primitives (`Int32`,
  `Float64`, `bool`), `Char`, `str`, `BigInt` (`int`), tuples, and user
  records that implement `ValueType`.
- `Any`.
- an explicit `Own[T]` for owning transfer of a non-value type.

Bare non-value types are rejected: non-value user records, `list[T]`,
`dict[K, V]`, `set[T]`, `bytes`, `bytearray`. Views are also rejected:
`Span[T]`, `Ptr[T]`, `Ref[T]`, `StrView`, `BytesView`. (`StrView` is rejected
even though `str` is allowed -- the view form has no place to borrow from
inside the dunder body, while `str` is owned-by-value.)

The default param/return convention for non-value types is by reference, but
`__getattr__`'s body computes a result with no place to borrow from --
ownership transfer (`Own[T]`) is the only well-defined non-value return
shape in v1.

Call sites consume `T`. With `T = Any`, the usual narrow-required UX applies.
With concrete value `T` (`str`, `Int32`), the result is directly typed.
With `Own[T]`, ownership transfers to the caller.

`__setattr__(self, name: str, value: V)`: `V` may be any TPy type. Assignment
sites coerce the RHS to `V`. With `V = Any`, the standard "into-Any" coercion
applies (D15 view-to-owned upgrade etc.). Assignment of a value not coercible
to `V` is a normal type error.

`__delattr__` has no value parameter.

The dunder return types are read once at sema and locked for the class. They are
not name-sensitive; `obj.foo` and `obj.bar` (both undeclared) have the same
static type `T`.

---

## Interactions

### `@readonly` methods

- A `@readonly` method may call `__getattr__` only if `__getattr__` is itself
  `@readonly` (or inferred so). Same rule as any other method call.
- A `@readonly` method calling `__setattr__` or `__delattr__` is a hard error --
  these are mutating by definition.
- The compiler does NOT look inside the dunder body to determine actual
  mutation; it trusts the declared `@readonly` signature. (Same as auto-readonly
  inference today: body-level inference is a separate pass.)

### Mutation propagation

`__setattr__` and `__delattr__` are normal mutating methods. Existing
mutation-propagation through call graphs (`sema/mutation_propagation.py`) handles
them with no special case -- a method that does `obj.foo = v` (where `foo` is
undeclared and the class is dyn-writable) becomes a method that calls a mutating
method on `obj`, and propagation flows accordingly.

### MRO / inheritance

Dunders are inherited via standard MRO. `class Child(Parent)` where `Parent`
declares `__getattr__`: `Child` is dyn-readable, dispatch goes to
`Parent.__getattr__`. Subclass may override the dunder; standard method
override rules apply. Multiple inheritance (D22): the first ancestor in MRO
declaring the dunder wins.

Resolution at access sites uses a shared "look up special method through MRO"
helper, parallel to existing method / property lookup. v1 has **no cached
presence flags** -- doing so is risky because (a) inheritance is finalized
after method registration, so a flag populated at registration time can miss
inherited dunders, and (b) generic-parent type substitution is access-site
dependent, so any cached binding would have to be re-resolved per call site
anyway. Just call the helper. If profiling later shows the lookup is hot,
caching can be added once *after* inheritance validation has run, storing
either local-declared booleans or post-MRO `FunctionInfo` slots.

Inherited dunders on generic parents (`class Box[T](Parent): ...` where
`Parent` declares `__getattr__ -> T`) work if the shared helper handles the
substitution naturally. Required v1 success criteria don't include this --
core v1 only proves local-dunder cases.

### `isinstance` narrowing on dyn-attr returns

`obj.foo` (when undeclared, routed through `__getattr__`) is a method call, not
a place expression. Narrowing requires binding to a local first:

```python
x = obj.foo
if isinstance(x, str):
    x.upper()
```

This is the same constraint as `@property` access. Direct narrowing on
`obj.foo` would call the dunder twice (with potentially different results) and
is not supported.

### Generics

A class with type params may declare dunders normally. The dunder return type
may reference class type params (e.g. `class Box[T]: def __getattr__(...) -> T`).
Standard generic method machinery applies.

### Static protocols

Static protocol conformance looks at *declared* members only. A class with
`__getattr__` does not structurally satisfy a protocol just because the protocol
declares a method named `bar` -- only declared `bar` counts. (CPython's runtime
duck typing arguably does match here, but TPy's static dispatch cannot
reasonably specialize through `__getattr__`.)

### `@dynamic` protocols

Out of scope for v1. A future extension could route `Adapter::call("bar", ...)`
through `__getattr__` when the underlying class is dyn-readable.

### `@nocopy` / `Own[T]`

Dunders are normal methods. `__setattr__(self, name: str, value: Own[T])` is
valid in principle, but is not part of the v1 test surface -- `Own[T]` value
parameters drag move semantics into a feature whose core is name routing.
Cover concrete-`V` and `Any`-`V` first; revisit after v1 is stable.

`__getattr__ -> Own[T]` follows the non-borrowed-return rule and is allowed.

---

## CPython Divergences

Numbered for the LANGUAGE_FEATURES note that will reference this list.

1. **`__setattr__` is fallback-only.** Declared-field writes bypass the dunder.
   CPython intercepts every assignment, which means CPython's `__init__` of a
   class with `__setattr__` defined recurses on `self._declared = value`
   unless the user uses `object.__setattr__` as the escape hatch. TPy never
   routes declared writes through the dunder, so the same source code takes
   different paths under the two backends and may behave differently. TPy
   does NOT recognize CPython's `object.__setattr__` escape syntax -- code
   that needs to run under both backends should either avoid defining
   `__setattr__` or accept the divergence (TPy-only via `no_cpython.txt`).

2. **`__getattribute__` not supported.** Use `__getattr__` for fallback or
   `@property` for per-attribute interception.

3. **No automatic per-instance `__dict__`.** `obj.__dict__`, `vars(obj)` are
   not available unless the user backs storage manually.

4. **`__getattr__` return type is statically declared.** Heterogeneous proxies
   must declare `-> Any` and have callers narrow. CPython's dynamic dispatch
   permits returning different concrete types per call without annotation.

5. **`@property` and `__getattr__` precedence.** `@property foo` is treated as
   a declared member -- always wins over `__getattr__`. CPython agrees, but the
   rule is stated explicitly here because TPy resolves it statically.

6. **Recursion within the dunder is identical to CPython, but failure mode
   differs.** A `__getattr__` body that touches an undeclared attribute on
   `self` recurses. Likewise a `__setattr__` body that writes an undeclared
   attribute on `self`. CPython raises `RecursionError`; TPy compiles to
   straight C++ recursion and crashes with stack overflow. Same gotcha, no
   safety net -- guard with `if name.startswith("_"): raise AttributeError(...)`
   or operate on a declared backing field.

7. **Builtin `getattr` / `setattr` / `delattr` are dynamic-fallback only.**
   `getattr(obj, "declared_field")` (literal name) is a compile error in
   TPy (use `obj.declared_field`); CPython evaluates it at runtime returning
   the value or bound method. Restriction lifts alongside method-reference
   support (`TODO.md:101`).

8. **Dynamic-name builtins always route to the dunder (Option A).**
   `getattr(obj, name_var)` (and `setattr`/`delattr`/`hasattr` with a
   non-literal name) call `__getattr__`/`__setattr__`/`__delattr__`
   unconditionally -- there is no static prefix dispatch over declared
   members. CPython divergence: code that does
   `getattr(obj, runtime_field_name)` expecting to read a declared field
   gets the dunder instead. Workaround: route declared-member access
   through the dunder body, or use direct attribute access. The
   alternative design (runtime string dispatch + dunder fallback) was
   rejected in favor of Option A because it would have reopened
   value-category questions for every declared member kind (bound
   methods, properties, class constants) and widened `getattr`'s static
   return type to `Any`.

9. **`del obj.declared_field` is rejected, never routed to `__delattr__`.**
   Declared field deletion is illegal in TPy regardless of whether
   `__delattr__` is defined (record layout is fixed). CPython routes
   `del obj.declared` through the user's `__delattr__` if defined. In TPy a
   user wanting that semantics must use a non-declared name, or implement the
   delete semantics via a method.

10. **`AttributeError` is a throw-tier exception** (inherits `Exception`).
    `raise AttributeError(name)` compiles to a normal C++ throw and
    propagates through the call stack via standard EH. `try/except
    AttributeError` catches as throw-tier; unhandled at top-level, the
    terminate handler prints "TurboPython panic: uncaught tpy::AttributeError:
    <name>". Modern table-based EH is zero-cost on the happy path; the
    fire path is microsecond-scale (matters only for hot probe-and-miss
    loops, which `hasattr` / 3-arg `getattr` handle internally with a
    single try/catch frame -- no propagation up the user's stack). This
    is a deliberate departure from the StopIteration / return-tier model
    used for `__next__`: an attribute miss is genuinely exceptional from
    the caller's perspective, not control flow, and this avoids the
    `@error_return`-coloring friction that would otherwise force every
    dyn-attr-using function to either decorate or wrap in try/except.
    CPython parity: `raise AttributeError(name)` works identically in
    any function on either backend.

---

## C++ Output

Routing is materialized at sema time as a synthetic method-call node, so the
existing codegen path emits ordinary method-call C++. v1 call-site rewrites:

```cpp
// class Config { ... __getattr__/__setattr__/__delattr__ ... };

// obj.foo  (undeclared, dyn-readable class)  ->
obj.__getattr__("foo")

// obj.foo = v  (undeclared, dyn-writable class)  ->
obj.__setattr__("foo", v)

// del obj.foo  (undeclared, dyn-deletable class)  ->
obj.__delattr__("foo")
```

`AttributeError` is a throw-tier exception. The dunders return their
declared `T`; missing-attribute is signaled via `raise AttributeError(name)`
which compiles to a normal C++ throw. Direct `obj.foo` access compiles
to a bare method call -- the throw propagates through the call stack
via standard EH. `hasattr` and 3-arg `getattr` wrap the dunder call in
a GCC stmt-expr block that converts the throw to a boolean / default
value:

```cpp
// hasattr(obj, "foo")  ->
({ bool __ok = true;
   try { (void)(obj.__getattr__("foo")); }
   catch (const ::tpy::AttributeError&) { __ok = false; }
   __ok; })

// getattr(obj, "foo", default)  ->
({ std::optional<T> __r;
   try { __r.emplace(obj.__getattr__("foo")); }
   catch (const ::tpy::AttributeError&) { __r.emplace(<default-coerced>); }
   std::move(*__r); })
```

GCC stmt-expr matches the rest of codegen (chained compares,
comprehensions). `std::optional<T>` defers initialization so T need not
be default-constructible; both arms `emplace` and the block yields
`std::move(*__r)`.

Cost note: throw-fire is microsecond-scale (vs nanos for return-tier),
which matters only for hot probe-and-miss loops. `hasattr` /
`getattr-default` catch one frame away (inside the stmt-expr block), so
they don't unwind your call stack on misses. Direct `obj.foo` access on
a missing attribute is a programmer error -- if unhandled it terminates
with a clean message via the runtime's `tpy_terminate_handler`.

---

## Implementation Notes

### Parser

- Lift the `ParseError("'del' on attributes is not supported")` at
  `parse/parser.py:3039`. Add a TPy AST node for attribute-delete (parallel to
  the existing subscript/local delete forms).

### Sema

The routing for all three dunders is materialized at sema time as a synthetic
`TpyMethodCall` node, parallel to how `@property` getters/setters route today.
This is load-bearing: it ensures `@readonly` enforcement, mutation
propagation, borrow conflict checks, and call-edge recording all see the
synthetic call and apply uniformly. Codegen-only rewrites would silently
bypass these.

- Signature validation (per the Trigger Rules section above): exact param
  counts, `name: str`, `__setattr__`/`__delattr__ -> None`, `__getattr__`
  return type per the Return / Value Types section. Reject `@staticmethod`,
  `@classmethod`, `@property`, `@overload`, `@error_return`, `async def`,
  generators (`yield` in body). No defaults / varargs / kwargs / method-level
  type params.
- MRO lookup: shared "look up special method through MRO" helper used at
  every access site. No cached presence flags in v1 -- correctness over
  microbenchmark; revisit if profiling demands.
- Attribute-access analysis (`sema/expressions.py`): after the existing static
  resolution path fails, consult the MRO helper for `__getattr__`; if found,
  synthesize a `TpyMethodCall`.
- Assignment analysis: parallel path on the write side. Important
  implementation order: do **not** call the standard expression analyzer on
  the assignment target first -- that would error on missing field access
  before the dunder path is consulted. Instead, detect `TpyFieldAccess`
  assignment targets, attempt static declared-member resolution, and only on
  miss synthesize the `__setattr__` `TpyMethodCall` (with the RHS type-checked
  against the dunder's `V` parameter type, including coercion).
- `del` statement analysis: parallel path for `__delattr__`. Same
  implementation-order caveat -- detect attribute-delete, attempt static
  declared-member resolution (which always errors for declared fields per the
  Lookup Semantics rules), and only on miss synthesize the `__delattr__`
  call. Depends on the parser plumbing above.
- Builtin-call dispatcher: `getattr` / `setattr` / `delattr` with the v1
  fallback-only restriction. Reject literal names that match any declared
  member (field, property, method, class constant).
- Generic / unresolved receiver types get static-only behavior; the dunder
  fallback only fires when the receiver type resolves to a concrete class.

### Codegen

- Routed dunder calls reuse `_gen_method_call` -- no new path.
- v1.5: `hasattr` / 3-arg `getattr` use a GCC stmt-expr block; the
  try/catch updates a local (`__ok` for hasattr, `std::optional<T> __r`
  for getattr-default) and the block yields that local as its value.

### Tests

`tests/cases/dynamic_attrs/` shipped 32 cases. Coverage by category (browse
the directory for the full list):

- **Happy paths**: `getattr_basic`, `getattr_concrete`, `setattr_basic`,
  `setattr_declared_bypass`, `delattr_basic`, `narrow_via_local`,
  `setattr_via_helper`, `readonly_getattr_ok`, `single_inheritance`,
  `multi_inheritance_dunder`, `inherited_setattr`, `inherited_delattr`.
- **Builtins**: `builtin_getattr_fallback`, `builtin_setattr_fallback`,
  `builtin_delattr_fallback`.
- **Declared-member rejections**: `error_getattr_on_declared`,
  `error_setattr_on_declared`, `error_delattr_on_declared`,
  `error_setattr_on_property`.
- **v1.5 builtins**: `hasattr_basic` (dyn-readable runtime check),
  `hasattr_static_members` (declared field/property/method/class-constant -> compile-time True),
  `hasattr_no_dunder` (no `__getattr__` -> compile-time True/False from declared set),
  `hasattr_inherited` (inherited `__getattr__` via MRO),
  `getattr_default_concrete` (3-arg with `T = str`),
  `getattr_default_any` (3-arg with `T = Any`, default coerced into Any),
  `getattr_default_optional` (3-arg with `T = Optional[X]`, None default),
  `getattr_in_try_except` (direct `obj.foo` inside try/except AttributeError).
- **Throw-tier integration**: `setattr_reject_caught`, `delattr_reject_caught`
  (dunder raises AttributeError, caller catches),
  `panic_getattr_unhandled`, `panic_setattr_reject_unhandled`
  (uncaught throws hit the terminate handler).
- **Throw-tier raise outside dunder**: `tests/cases/exceptions/throw_attribute_error/`
  (regular function raising AttributeError, caller catches).
- **Dunder validation rejections**: `error_static_getattr` (`@staticmethod`),
  `error_overloaded_getattr` (`@overload`), `error_borrowed_return`,
  `error_bare_record_return`, `error_error_return_getattr` (`@error_return`),
  `error_native_getattr` (`@native` records).
- **Cross-cutting rejections**: `error_no_getattr` (undeclared on static
  class), `error_no_setattr` (write without `__setattr__`),
  `error_no_delattr` (del without `__delattr__`), `error_readonly_setattr`
  (`@readonly` method calling `__setattr__`), `error_readonly_getattr`
  (`@readonly` method calling non-readonly `__getattr__`),
  `error_dyn_setattr_on_readonly` (synth `__setattr__` rejected on a
  `readonly[Bag]` receiver -- verifies the synth call participates in
  readonly enforcement), `error_protocol_no_dunder_match` (protocol
  conformance ignores dunder).

- **v1.5 builtin error**: `error_getattr_default_type_mismatch` (default
  not coercible to dunder return type).

- **Phase 9 -- dynamic-name builtins**: `dyn_name_getattr`,
  `dyn_name_getattr_default`, `dyn_name_hasattr`, `dyn_name_setattr`,
  `dyn_name_delattr`, `dyn_name_routes_through_dunder` (Option A:
  `getattr(obj, "declared", default)` with runtime name routes through
  `__getattr__` even when the name matches a declared field; CPython
  divergence). Sema rejection tests when the matching dunder is missing:
  `error_dyn_name_on_non_dyn_class` (getattr),
  `error_dyn_name_setattr_no_dunder`,
  `error_dyn_name_delattr_no_dunder`,
  `error_dyn_name_hasattr_no_dunder`.
Deferred indefinitely: `nocopy_value/` -- `__setattr__(self, name, value:
Own[T])` drags move semantics into the test surface; not core to dyn-attr
correctness. `inherited_generic/` (generic parent's `__getattr__ -> T`)
not part of v1 success criteria; works in practice via the MRO helper's
type substitution but no dedicated test yet.

CPython compatibility: tests that define `__setattr__` are TPy-only
(`no_cpython.txt`) because the test class's `__init__` writes declared
fields with plain `self.field = value` -- which TPy bypasses (fallback-only)
but CPython routes through the user's `__setattr__`, recursing. Tests that
only define `__getattr__` (or no dunders at all) run under both backends
and verify CPython parity for the read-side / declared-member cases.
