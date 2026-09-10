# Any Type Design

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | `AnyType` in type system, `@builtin_type("typing.Any")` stub, parser/resolver, composition rules | Done |
| **Phase 2** | Runtime: `tpy::Any` wrapper around `std::any` + per-type `AnyOps` table | Done |
| **Phase 3** | Codegen: into-Any conversion, `AnyOps` instantiation per stored type, copyable-only enforcement | Done |
| **Phase 4** | Universal ops: `print`, `str`, `repr`, f-string, `bool`, `==`/`!=`, `hash`, `x is None` | Done |
| **Phase 5** | `typing.cast(T, x)` runtime semantics on `Any`: checked extraction; mismatch raises `TypeError` (catchable) | Done |
| **Phase 6** | `isinstance(x, T)` non-consuming borrow narrowing on `Any` | Done |
| **Phase 7** | Auto-coerce in known-target contexts (assignment, args, return, container insert) | Done |
| **Phase 8** | Narrow-required diagnostics, `set[Any]` / `dict[Any, V]` runtime hash check (`@noalloc` rejection deferred until broader `@noalloc` enforcement lands) | Done |
| **Phase 9** | Tests (unit-level). No CPython shim needed. | Done |

### Future Extensions

| Feature | Notes |
|---------|-------|
| Binary-op auto-coerce (`any + 1`, `any < 5`, etc.) | Drags `Any` into overload/operator resolution; turns simple expressions into runtime panic sites. Defer until concrete need. |
| Move-only contents (`Own[T]`, `@nocopy` records) | `std::any` requires `CopyConstructible`. Adding move-only support means switching to a custom `tpy::Any` with a richer vtable (destroy/move slots). Big change; defer until a real workload needs it. |
| `x is y` for general `Any` operands | Byte-compare or pointer-compare doesn't match Python's identity. Only `x is None` is allowed in v1. |
| Inheritance-aware `cast` / `isinstance` (walk bases) | Requires runtime base-class table. v1 is exact-type only. |
| Method-name dispatch on raw `Any` | Effectively Python `object`. Users narrow first. |
| Subscript / iteration / call / `len(any_var)` on raw `Any` | Same reasoning -- narrow first. |
| F-string format spec (`f"{any_var:>10}"`) | Needs richer dispatch than the print slot. |
| Stdlib stubs returning `Any` (`json.loads`, `pickle`, etc.) | Exercised after v1 lands. Most are better served by typed alternatives (recursive ADTs, macros) -- only add `Any`-typed stubs when no alternative fits. |
| Pickling / serialization | Out of scope. |
| Cross-type numeric equality on `Any` (`Any(1) == Any(1.0)` -> True) | Would need a "category" tag on `AnyOps` and cross-promotion logic in the equals slot. Cost on every `==`; CPython compat win is real but rarely matters in the dict[str, Any] use case. Defer; users extract first. |
| TPy-friendly type names in `cast`/`hash` exception messages | Messages now go through `tpy::demangle_type_name(typeid::name())` (see `core.hpp`), so users see `tpy::BigInt` and `std::__cxx1112basic_string<...>` instead of the mangled form. The standard-library types are still verbose; a TPy-side typeid -> friendly-name registry seeded by `any_ops_for<T>` instantiations would render `str` instead of `std::__cxx11::basic_string<...>`. Defer until the verbose names cause real friction. |

## Vision

`Any` is a **type-erased value cell** for cases where the static type cannot
reasonably be expressed at the declaration site:

1. **Dynamic configuration / argument systems**. When the schema is
   determined at runtime (config-driven CLI, plugin-registered options, an
   external schema document), `dict[str, Any]` is the natural shape and each
   access site is statically typed:

   ```python
   from typing import cast
   args: dict[str, Any] = parse_dynamic_config(spec)
   host: str = args["host"]                  # auto-coerce: runtime cast
   port: int = args["port"]
   debug: bool = args.get("debug", False)
   timeout = cast(float, args.get("timeout", 1.0))   # explicit
   ```

2. **Migration / incremental typing**. Porting Python code that lacks
   annotations -- `Any` is the "not yet typed" placeholder.

3. **Heterogeneous storage where no closed union or generic fits**. Rare,
   but a real escape hatch.

> **Out of scope for `Any`** (these have better-typed alternatives in TPy):
> - `json.loads` -- use the recursive `JsonValue` ADT (D19/D20).
> - `argparse.Namespace` -- use the macro API to declare typed arg sets at compile time.
> - `pickle`, `copy.deepcopy` -- skip / use existing typed signatures.

## Design Principles

1. **Owned value cell.** `Any` owns its contents (via `std::any`). Putting
   a value in copies it in (or moves at last use, per existing move
   semantics). The `Any` destructor destroys the contained value.
   Note: this is a **documented divergence from CPython** for record
   types -- in CPython, `a: Any = my_record` is reference assignment so
   `my_record.field = X` shows through `a`. TPy's runtime-wrapper Any
   copies the record at storage, so subsequent mutations of the
   original don't reach the Any-held copy. The trade-off is owned-cell
   guarantees (no dangling references) for the loss of shared-mutation
   semantics. Pinned by `tests/cases/any/record_storage_copies/`.
2. **Copyable contents only in v1.** Move-only types (`Own[T]`,
   `@nocopy` records) are rejected at compile time. This is what allows the
   `std::any`-backed implementation; supporting move-only contents would
   require a custom wrapper and is deferred.
3. **Static narrowing remains first-class.** Where the target type is
   statically known, the compiler auto-coerces with a runtime check. Where
   it is not, the user narrows via `isinstance` or `typing.cast`.
4. **Non-consuming `isinstance`.** `isinstance(any_var, T)` does *not*
   consume the `Any`. Inside the true branch, the user sees a borrow
   (`T const&` for value types, `T&` if mutation is needed) that aliases
   the contents. The `Any` remains valid before, during, and after the
   branch.
5. **A small set of universal operations always works** -- printing,
   truthiness, equality, hash. Implemented via a per-type `AnyOps` table
   alongside `std::any`. The set is intentionally small; method calls,
   attribute access, subscript, iteration, etc. require narrowing.
6. **Standard Python imports.** `typing.Any` and `typing.cast` are the
   public surface. In CPython, `typing.cast` is a static no-op (returns
   `x` unchanged); in TPy compiled binaries it gets runtime
   panic-on-mismatch semantics. The divergence is invisible in practice:
   mypy/pyright already cannot check casts (cast is by design unchecked
   statically), happy-path uses behave identically, and panic-test cases
   skip the cpy phase by convention. No `lib/cpy/` shim needed.

## Type System Integration

### `AnyType` in `typesys.py`

A new `TpyType` subclass:

```python
@dataclass(frozen=True, slots=True)
class AnyType(TpyType):
    @classmethod
    def get_canonical_name(cls) -> str:
        return "Any"
    # ... standard hooks (cpp_type -> "tpy::Any", supports_eq=True, ...)
```

Singleton instance reachable as `AnyType()`. Maps to C++ `tpy::Any`.

`AnyType` participates in TPy's value/reference distinction as a
**move-aware copyable type** -- like `tpy::List<T>` and similar wrappers, it
is copyable but the runtime cost is non-trivial, so existing move semantics
will auto-`std::move` at last use.

### Stub registration

In `lib/tpy/tpy/_typing/__init__.py`:

```python
@builtin_type("typing.Any")
class Any:
    """Type-erased value. Holds any concrete copyable value.
    Use `typing.cast(T, x)` or `isinstance(x, T)` to extract."""
```

Re-exported from `lib/tpy/typing.py` alongside `Optional`, `Union`, etc.

### `typing.cast` runtime semantics

The compiler recognizes `typing.cast(T, x)` and gives it runtime check
semantics when applied to `Any` (Phase 5). For non-`Any` sources, `cast`
remains a no-op (matches CPython's static-only semantics).

No new builtin name is introduced; users write `from typing import cast`
exactly as in standard Python. CPython runs see the standard static no-op
behavior. The divergence is acceptable because:

- `typing.cast` is *designed* to be unchecked; mypy/pyright cannot flag a
  wrong cast on `Any`. The TPy runtime check is *additional* safety that
  CPython simply doesn't have.
- Auto-coerce paths already produce the same divergence
  (`n: int = any_var` panics in TPy, silently assigns in CPython). Treating
  explicit `cast` the same way is internally consistent.
- `panic_{name}/` test cases skip the cpy phase by convention, so test
  authoring is unaffected.

### Resolution

`parse/type_resolver.py:_resolve_single_type` adds a special case for
`typing.Any` -> `AnyType()`, mirroring `typing.Optional`.

### Composition rules

| Construct | Behavior |
|---|---|
| `Any \| None`, `Optional[Any]` | Reject. `Any` already accepts `None`. |
| `Any \| T` for any other `T` | Reject. `Any` is the universal supertype. |
| `list[Any]`, `dict[K, Any]`, `set[Any]`, `tuple[Any, ...]` | Allowed. |
| `dict[Any, V]` | Allowed; runtime panic on insert if contained type is not hashable. |
| `set[Any]` | Allowed; same runtime hashable check. |
| `readonly[Any]` | Allowed; immutable view. |
| `Own[Any]` | Reject as redundant -- `Any` is already owning. |
| `Any` as record field | Allowed (~24-32 bytes). |
| `Any` as function param/return/local | Allowed. |
| `isinstance(x, Any)` | Reject -- `Any` is not a runtime class. Matches Python's `TypeError`. |
| `Any(value)` | Allowed as TPy-specific sugar for the INTO_ANY coercion -- lowers to `make_any(...)`. Useful inline (`[Any(p), Any(q)]`) without a typed intermediate. CPython raises `TypeError: Any cannot be instantiated`; this is a documented divergence (panic-test cases skip the cpy phase by convention). `Any()` and `Any(a, b)` rejected for arity; `Any(x)` where x is already Any rejected as redundant. |
| `Any` storing a value whose underlying contents type is non-copyable (`@nocopy`, `__del__`, also through `Own[T]` wrapping) | Reject at compile time: "cannot store move-only type T in Any (v1 supports copyable contents only)". `Own[T]` of a copyable T is allowed -- the contents are copyable, the move from the consumed handle is fine. |

## Runtime Representation

### Layout

```cpp
namespace tpy {

struct AnyOps;  // see below

struct Any {
    std::any value;          // 16-24 bytes (libstdc++: 16; MSVC: 32). Storage + RTTI + copy/move/destroy via std::any's manager.
    const AnyOps* ops;       // 8 bytes. Print/str/repr/bool/equals/hash dispatch.
};

}  // namespace tpy
```

Total size: 24-32 bytes depending on STL. `std::any` carries its own
small-buffer optimization (typically 16 bytes inline; libstdc++ stores
larger values on the heap). We piggyback on its allocation behavior --
no separate SBO threshold to maintain.

### `AnyOps` table

```cpp
namespace tpy {

struct AnyOps {
    void (*print)(std::ostream&, const std::any&);
    void (*str)(std::string&, const std::any&);
    void (*repr)(std::string&, const std::any&);
    bool (*to_bool)(const std::any&);
    bool (*equals)(const std::any& a, const std::any& b);  // null if T not Eq
    std::size_t (*hash)(const std::any&);                   // null if T not Hashable
};

template <typename T>
inline constexpr AnyOps any_ops_for = {
    /*print=*/[](std::ostream& os, const std::any& a) {
        tpy::print(os, std::any_cast<const T&>(a));
    },
    /*str=*/[](std::string& buf, const std::any& a) {
        tpy::format_str(buf, std::any_cast<const T&>(a));
    },
    /*repr=*/[](std::string& buf, const std::any& a) {
        tpy::format_repr(buf, std::any_cast<const T&>(a));
    },
    /*to_bool=*/[](const std::any& a) {
        return tpy::truthy(std::any_cast<const T&>(a));
    },
    /*equals=*/AnyOpsImpl<T>::equals_or_null,   // null if !concept_eq<T>
    /*hash=*/AnyOpsImpl<T>::hash_or_null,       // null if !concept_hashable<T>
};

}
```

**Conditional slot generation**: every slot is concept-checked and gets
either the real op or a sensible fallback:
- `print` / `str` / `repr` prefer `__str__(T)` / `__repr__(T)`; if neither
  exists they emit `<typeid object>` (Python's default `<ClassName object
  at 0x...>` -- we render the typeid's mangled name since the runtime has
  no class-name registry).
- `to_bool` defaults to `true` (matches Python's "objects without
  `__bool__`/`__len__` are truthy" rule).
- `equals` and `hash` stay null when T isn't Eq / Hashable; the runtime
  paths handle nulls (== returns False; hash() panics).

The `to_bool` concept mirrors `tpy::to_bool`'s acceptance criteria
directly rather than going through `tpy::to_bool` itself, because the
latter contains a `static_assert(false, ...)` that fires eagerly for
non-capable T at concept-check time.

`AnyOps` is `6 * sizeof(void*) = 48 bytes per type`, stored in `.rodata`,
deduped across TUs via `inline constexpr`.

### Empty / moved-from `Any`

`std::any::has_value()` is the empty check. Empty arises only from:
1. Move-out via `typing.cast` rvalue overload (consuming form -- discussed below).
2. `std::any` move-construct / move-assign leaving the source empty.

Default construction of `Any` is **not allowed** in TPy source -- consistent
with TPy's no-default-init rule for non-trivial types.

Accessing an empty `Any` (any vtable slot, `==`, etc.) panics:

> `panic: use of empty/moved-from Any`

Exception: `x is None` on an empty `Any` returns False (the empty state is
not `None`; use `is None` to check for stored None, not for moved-out).

### `None` in `Any`

`None` is stored as a real value of type `tpy::NoneType` with its own
`AnyOps` instantiation. Distinct from the empty/moved-from state.

- `x is None` -> `x.value.has_value() && x.value.type() == typeid(tpy::NoneType)`.
- `cast(NoneType, x)` extracts.
- `isinstance(x, type(None))` narrows.
- `bool(x)` returns False when `x` holds None (NoneType's `truthy` returns False).
- `dict[str, Any]` distinguishes "key absent" from "key maps to None".

## Operations

### Always-works (vtable / std::any dispatch)

These work on raw `Any` without narrowing:

| Python | C++ codegen sketch |
|---|---|
| `print(x)` | `x.ops->print(std::cout, x.value); std::cout << '\n';` |
| `str(x)` | `tpy::any_to_str(x)` (uses `str` slot, returns `std::string`) |
| `repr(x)` | `tpy::any_to_repr(x)` |
| `f"{x}"` | reuses `print` / `str` slot |
| `bool(x)`, `if x:`, `not x` | `x.ops->to_bool(x.value)` |
| `x == y`, `x != y` | `tpy::any_eq(x, y)` -- type mismatch / null slot -> False |
| `hash(x)` | `tpy::any_hash(x)` -- raises `TypeError("unhashable type: '<demangled>'")` if `ops->hash == nullptr` (catchable) |
| `x is None`, `x is not None` | `x.value.has_value() && x.value.type() == typeid(NoneType)` |

`Any` automatically satisfies the `Hashable` and `Eq` concepts at the
*type system* level, but *runtime* hash may raise `TypeError` (catchable)
if the contained concrete type doesn't support the operation. `==` for
mismatched types returns False (no exception). This is the trade-off for
allowing storage of arbitrary types.

**`==` semantics**: `tpy::any_eq` checks `a.value.type() == b.value.type()`
first; if they differ, returns False. Otherwise dispatches to the `equals`
slot. If the `equals` slot is null (T isn't Eq), returns False (the only
honest answer when T has no notion of equality).

**Divergence from CPython**: typeid-based equality means `Any(1) == Any(1.0)`
is **False** in TPy, where CPython returns True. Same for `Any(True) == Any(1)`
and `hash(Any(1)) != hash(Any(1.0))`. This is consistent with the auto-coerce
divergence (`n: int = any_var` raises `TypeError` in TPy where CPython silently assigns) --
TPy treats stored typeid as part of the equality and hash identity. Cross-type
numeric equality requires extracting first (`cast(float, x) == 1.0`).
A regression test pins this behavior so it isn't accidentally "fixed" toward
CPython parity. (Full Python numeric coercion across `Any` operands is in
Future Extensions.) The sibling type-erased form has since gone the other
way: a value union compares BY VALUE across alternatives through
`::tpy::Union`'s own operators, so `Int32 | Float64` holding 1 equals one
holding 1.0. The
two forms therefore disagree today; `BUGS.md#any-eq-compares-typeid-not-value`
tracks settling `Any` the same way.

**`x is None` only**: `is` for any other RHS is a compile error in v1.
Worded diagnostic: `is is only supported with None on Any -- did you mean
== or isinstance?`. (See Future Extensions for the general case.)

### Auto-coerce (deduced target type)

When the target type is statically known, the compiler inserts the same
runtime extract used by explicit `typing.cast` and raises `TypeError` on
mismatch (catchable).

```python
host: str = cfg["host"]                 # cast(str, cfg["host"])
def f(n: int) -> None: ...
f(any_var)                              # cast(int, any_var)
return any_var                          # in `-> int` function
lst: list[int] = []
lst.append(any_var)                     # cast(int, any_var)
my_dict["k"] = any_var                  # cast(V, any_var) for dict[str, V]
```

**Trigger contexts**:
1. Assignment with explicit annotation on LHS.
2. Function call argument where the callee has a single, unambiguous param
   type at that position. **Overload sets disable auto-coerce.**
3. `return any_var` where the function return type is concrete (not `Any`,
   not a generic type parameter inferred from the `Any` argument).
4. Container insertion (`list.append`, `dict[k] = v`, `set.add`, tuple
   construction).

**Non-triggers**:
- Bare `x = any_var` (no annotation; `x` picks up `Any`).
- Generic functions where the type parameter is inferred from the `Any` arg.
- Binary operators with `Any` (deferred -- see Future Extensions).

### Narrow-required (compile error on raw `Any`)

| Operation | Why |
|---|---|
| `any_var.attr` / `any_var.method()` | Don't know what type has this attr/method |
| `any_var(args)` | Don't know if it's callable or what signature |
| `for x in any_var:` | Don't know if it's iterable or element type |
| `any_var[k]` | Don't know if it's subscriptable or key/value types |
| `len(any_var)` | Not all types have `__len__` |
| `any_var + 1` (and other binops) | Deferred: see Future Extensions |
| `any_var is x` for `x` other than `None` | Identity semantics on type-erased values are not Python `is` |
| `+any_var`, `-any_var`, `~any_var` | No deducible result type |

Single uniform diagnostic:

> `error: cannot {operation} on value of type Any -- narrow first via isinstance(x, T) or typing.cast(T, x)`

After narrowing, the variable has a concrete type and all operations work.

## Coercion Rules

### Into `Any` (always implicit, never fails)

```python
a: Any = 42                    # implicit: tpy::Any::from(int64_t{42})
a: Any = "hello"               # implicit: stored as owned std::string (see below)
a: Any = my_record             # implicit copy / move-at-last-use
a: Any = None                  # implicit: tpy::Any::from(NoneType{})
lst: list[Any] = [1, "two", 3.0]  # each element converts
```

If the source's underlying contents type is non-copyable (`@nocopy`,
`__del__`, also through `Own[T]` wrapping), this is a **compile error** in
v1 (see Future Extensions). Note that `Own[T]` of a copyable T is fine --
the contents are copyable; the move from the consumed handle into the
`Any`'s storage is the natural codegen.

#### Copy warning for reference-type sources

Storing a reference type (`record`, `list`, `dict`, `set`) into `Any`
silently copies (Principle #1: `Any` owns its contents). That contradicts
TPy's reference-type baseline -- elsewhere a copy of a record/container
requires explicit `copy()`. The compiler warns at the storage site and
suggests the explicit form:

```python
n = Node(1)
a: Any = n               # warning: copies Node into Any; use copy() to make this explicit
print(n.n)               # n still alive -- mutations through n don't reach a

a: Any = copy(n)         # ok: explicit copy acknowledged
a: Any = Node(2)         # ok: rvalue, no other handle exists

xs: list[Any] = [n]      # warning: element n is an lvalue
xs: list[Any] = [Node(3)]  # ok: element is an rvalue
```

The check fires uniformly across every INTO_ANY site: annotated
assignment (`a: Any = ...`), function argument (`f(x)` where the
parameter is `Any`), return value (`return x` where return type is `Any`),
container-literal element (`[x]: list[Any]`, `{"k": x}: dict[str, Any]`),
and subscript / field assignment when the target slot is `Any` (`d["k"]
= x`, `holder.payload = x`).

**Suppression rules** (mirror the dict.update / iter copy-warning
machinery):
- Source wrapped in `copy()` -- explicit acknowledgement.
- Rvalue source (call result, constructor call) -- no other handle exists,
  so no observable divergence. Container literals are not blanket rvalue
  sources: each element is checked independently.
- Last use of an owned local -- auto-move makes the copy invisible.

**Silent (no warning)**: primitives, value-type records, `str` (collapses
to `std::string` at INTO_ANY), `bytes`/`BytesView` (Python-immutable
semantics make share-vs-copy unobservable), `Ptr[T]` (storing the
address, not the pointee).

#### Owning conversion at the storage site

`Any` owns its contents, but several TPy types are non-owning views (`str`
is `std::string_view`, `bytes`/`BytesView` is `std::span<const uint8_t>`).
Storing a view directly would silently extend nothing -- the underlying
buffer can die while the `Any` still references it.

**Rule**: at the into-Any conversion site, view types are converted to their
owning equivalent before storage. The typeid stored inside `std::any` is the
owning type, not the view type.

| Source static type | Stored type (typeid) | Notes |
|---|---|---|
| `str` / `StrView` (`std::string_view`) | `std::string` | Copies the view contents into an owned string. |
| `bytes` / `BytesView` (`std::span<const uint8_t>`) | `tpy::Bytes` | Copies into the owning bytes type. |
| All other copyable types | unchanged | Stored as-is. |

Symmetrically, `cast(str, x)` and `isinstance(x, str)` check
`typeid(std::string)` and expose a `StrView` borrowed from the contained
`std::string`. The borrow lifetime is the lifetime of the `Any` (same model
as `T const&` borrows from records). `cast(bytes, x)` works identically
against `tpy::Bytes`.

This means there is no way to store a non-owning view in `Any` -- the
view-vs-owned distinction collapses inside `Any`. Users see `str`/`bytes`
on both sides and don't have to think about it.

### Out of `Any`

| Mechanism | Static check | Runtime check |
|---|---|---|
| Auto-coerce (annotated LHS, etc.) | Target type is concrete and unambiguous | `value.type() == typeid(T)`; raises `TypeError` (catchable) on miss |
| `typing.cast(T, x)` | `T` must be a concrete type | Same; raises `TypeError` on miss |
| `isinstance(x, T)` narrowing | `T` concrete; inside the true branch, `x` is `T` (borrow) | `value.type() == typeid(T)`; false branch never raises |

### `typing.cast(T, x)` semantics

Codegen:

```cpp
template <typename T>
T any_cast_or_panic(const Any& a) {
    if (a.empty()) tpy_panic("use of empty/moved-from Any");
    if (a.value.type() != typeid(T)) {
        raise<TypeError>("Any holds {}, cannot cast to {}",
                         demangle_type_name(a.value.type().name()),
                         demangle_type_name(typeid(T).name()));
    }
    return std::any_cast<T>(a.value);  // copy
}

template <typename T>
T any_cast_or_panic(Any&& a) {
    if (a.empty()) tpy_panic(...);
    if (a.value.type() != typeid(T)) raise<TypeError>(...);
    return std::any_cast<T>(std::move(a.value));  // move-out
}
```

The empty/moved-from check stays as `tpy_panic` (use-after-move is an
internal lifetime invariant, not a Python exception). The type-mismatch
check raises `TypeError` -- catchable by user code.

The const-ref overload **copies** the contents -- auto-coerce paths and
explicit `typing.cast` always emit this form in v1. The rvalue overload
exists for future move-out-at-last-use optimisation, but codegen does
not currently emit `std::move` on the source expression; wiring last-use
detection through the FROM_ANY coercion lambda is deferred. Functionally
correct (just an extra copy when the source is dead afterward).

### `isinstance(x, T)` narrowing -- non-consuming

```cpp
if (any_var.value.has_value() && any_var.value.type() == typeid(T)) {
    const T& any_var = std::any_cast<const T&>(/* outer */ any_var.value);
    // ... use any_var as T inside the branch
}
```

The narrowed name shadows the outer `Any` for the branch lifetime. The outer
`Any` remains alive and intact -- usable before, during (via the borrow), and
after the branch. This matches Python's `isinstance` intuition.

A mutable narrowing variant (binding to `T&` for in-place mutation of the
contained value) is described in the design but not implemented in v1.
Codegen always emits `const T&` regardless of whether the source `Any` is
a mutable lvalue. Defer until a real workload needs it.

If the user wants to *take ownership* of the contained value (consuming
extract), they call `typing.cast(T, x)` explicitly inside the branch.
The rvalue-overload move-out optimisation is not currently emitted by
codegen (see "Coercion Rules / `typing.cast(T, x)` semantics" above);
the const-ref overload always fires, producing one extra copy.

## Codegen Patterns

### Constructing `Any`

```python
a: Any = 42
```

```cpp
tpy::Any a{std::any{int64_t{42}}, &tpy::any_ops_for<int64_t>};
```

### `print(any_var)`

```cpp
any_var.ops->print(std::cout, any_var.value);
std::cout << '\n';
```

### Auto-coerce

```python
n: int = any_var
```

```cpp
int64_t n = tpy::any_cast_or_panic<int64_t>(any_var);
```

### `isinstance` narrowing

```python
if isinstance(any_var, int):
    print(any_var + 1)
```

```cpp
if (any_var.value.has_value() && any_var.value.type() == typeid(int64_t)) {
    const int64_t& any_var_n = std::any_cast<const int64_t&>(any_var.value);
    tpy::print(std::cout, any_var_n + 1);
    std::cout << '\n';
}
// any_var still alive here
```

(Variable name in the narrowed scope is internally renamed; the user-visible
name maps to the borrow during analysis.)

### `==` via vtable

```python
if any_var == other:
    ...
```

```cpp
if (tpy::any_eq(any_var, other)) { ... }

// For Any-vs-concrete:
template <typename U>
bool any_eq_concrete(const Any& a, const U& b) {
    if (!a.value.has_value()) return false;
    if (a.value.type() != typeid(U)) return false;
    return std::any_cast<const U&>(a.value) == b;
}

// For Any-vs-Any:
inline bool any_eq(const Any& a, const Any& b) {
    if (!a.value.has_value() || !b.value.has_value()) return false;
    if (a.value.type() != b.value.type()) return false;
    if (!a.ops->equals) return false;  // T not Eq
    return a.ops->equals(a.value, b.value);
}
```

### `x is None`

```cpp
bool is_none = any_var.value.has_value()
            && any_var.value.type() == typeid(tpy::NoneType);
```

## `@noalloc` Interaction

`std::any` may allocate for non-trivial types (anything larger than its
SBO), so `Any` semantically conflicts with `@noalloc`. **Not currently
enforced**: TPy's `@noalloc` decorator is parsed and stored on the
function info but doesn't policy any allocation behaviour today (lists,
dicts, strings — all heap-allocating — flow freely). Adding a one-off
rejection just for `Any` would create a misleading inconsistency where
`@noalloc` looks like it enforces something it doesn't.

When proper `@noalloc` enforcement lands, `Any` should join the reject
list (signature *and* body locals) with a clear "Any not allowed in
`@noalloc` function" diagnostic. SBO-aware refinement (allow `Any`
when all flowing types are guaranteed to fit `std::any`'s small-buffer
optimisation) is a possible follow-up; defer the complexity.

## Generics

### `list[Any]`, `dict[K, Any]`, `tuple[Any, ...]`

Standard generic instantiation. Element / value type is `tpy::Any`.

### `set[Any]`, `dict[Any, V]`

Allowed. Hash is required for keys. Strategy: at insert time, check
`value.ops->hash != nullptr`; if null, raise `TypeError` (catchable):

> `unhashable type: '{demangled-type}'`

Empty `Any` panics on insert (`use of empty/moved-from Any`) -- internal
lifetime invariant, not a Python exception.

### `Any` field in user records

```python
class Bag:
    items: list[Any]
    metadata: Any
```

Fields work normally. Records containing `Any` remain copyable (since
`Any` is copyable in v1). No special propagation needed.

### `Any` and protocols

`Any` automatically satisfies `Hashable + Eq` at the type system level
(`hash` may raise `TypeError` at runtime if the contained type isn't
Hashable; `==` returns False rather than raising on mismatch).

`Any` does **not** satisfy `Iterable[T]`, `Sized`, etc. -- those require
narrowing.

## Errors and Diagnostics

The narrow-required ops on raw `Any` reuse TPy's existing
type-rejection machinery -- the per-op messages are concise ("Cannot
call method on type Any", etc.) without the "narrow first via
isinstance(x, T) or typing.cast(T, x)" hint sketched below. The hint
phrasing is a future cosmetic refinement and is not blocking.

| Situation | Diagnostic |
|---|---|
| `any_var.method()` | `Cannot call method '{name}' on type Any` |
| `any_var.field` | `Cannot access field '{name}' on type Any` |
| `any_var()` | `'{name}' is not callable` |
| `for x in any_var` | `Cannot iterate over type Any` |
| `any_var[k]` | `Cannot index type Any` |
| `len(any_var)` | `No matching overload for len(Any)` |
| `any_var + x` (any binop) | `Invalid operand types for '+': Any and {other}` |
| `any_var is x` for `x != None` | `'is' / 'is not' can only compare Optional/Ptr/union types with None, got Any and {other}` |
| `cast(T, x)` runtime mismatch | `TypeError: Any holds {actual}, cannot cast to {T}` (catchable) |
| Storing a non-copyable contents type in Any (`@nocopy`, `__del__`, also through `Own[T]`) | `cannot store move-only type 'T' in Any (v1 supports copyable contents only)` |
| `Any \| None`, `Optional[Any]` | `Any \| None is redundant -- Any already accepts None` |
| `Any \| T` for other T | `Any \| T is redundant -- Any is the universal supertype` |
| `Own[Any]` | `Own[Any] is redundant -- Any is already owning` |
| `cast(Any, x)` (incl. aliased imports) | `typing.cast(Any, ...) is meaningless -- pick a concrete type` |
| `cast(int \| str, x)` (union target) | `typing.cast() target must be a concrete type, not a union` |
| `==` on Any holding non-Eq T | Returns False (no panic) |
| `hash` on Any holding non-Hashable T | `panic: cannot hash Any holding {type} -- {type} is not Hashable` |
| `set[Any].add(x)` for non-hashable contents | Same panic on insert |
| Use of empty/moved-from `Any` | `panic: use of empty/moved-from Any` |
| Overload ambiguity blocking auto-coerce | `cannot auto-coerce Any to argument of overloaded {fn} -- use typing.cast(T, x)` |

## Implementation Phases

### Phase 1: Type system (sema-only)

- Add `AnyType` to `typesys.py`.
- Register `@builtin_type("typing.Any")` stub in `lib/tpy/tpy/_typing/`.
- Resolver maps `typing.Any` -> `AnyType()`.
- Composition rules (reject `Any | None`, `Own[Any]`, etc.).
- Reject move-only contents at type-check time.

### Phase 2: Runtime header

- Write `runtime/cpp/include/tpy/any.hpp`.
- `tpy::Any` struct (`std::any` + ops pointer).
- `AnyOps` struct.
- `any_ops_for<T>` template (`inline constexpr`).
- `any_cast_or_panic<T>` (const-ref + rvalue overloads), `any_eq`,
  `any_eq_concrete<U>`, `any_hash`.
- Concept-checked conditional slot generation for `equals` / `hash`.

### Phase 3: Codegen for storage

- Emit `tpy::Any{std::any{value}, &any_ops_for<T>}` on into-Any conversion.
- Vtable instantiations dedup automatically via `inline constexpr`.
- Move semantics: into-Any conversions use copy or move based on liveness
  (existing infra).
- Compile-time copyable-only enforcement.

### Phase 4: Universal ops codegen

- `print`, `str`, `repr`, f-string -> ops slot dispatch.
- `bool(x)`, `if x:`, `not x` -> `to_bool` slot.
- `==`, `!=` -> `any_eq` / `any_eq_concrete`.
- `hash(x)` -> `any_hash`.
- `x is None` / `x is not None` -> typeid check.

### Phase 5: `typing.cast` runtime semantics

- Recognize `typing.cast(T, x)` in sema; require `T` to be a concrete type.
- Codegen: emit `tpy::any_cast_or_panic<T>(...)` for the `Any` case;
  for non-`Any` sources, treat as a no-op (matches CPython's static-only
  semantics).
- No new builtin name introduced -- users write `from typing import cast`
  exactly as in standard Python.
- No CPython shim. The runtime divergence (TPy panics vs CPython silent
  pass-through) is invisible: mypy can't check casts anyway, happy paths
  match, panic-test cases skip cpy phase.

### Phase 6: `isinstance` on `Any`

- Extend `_analyze_isinstance` in `sema/calls.py`.
- Narrowing: borrow exposing `T const&` (or `T&` for mutable lvalue Any) in
  the true branch. Outer `Any` remains alive.
- Codegen: emit typeid compare + std::any_cast<const T&>.

### Phase 7: Auto-coerce

- Sema: in assignment / arg / return / container insert contexts where
  target is concrete and source is `Any`, accept the conversion and emit
  `any_cast_or_panic` codegen.
- Overload ambiguity: reject with the auto-coerce diagnostic.

### Phase 8: Errors + set/dict-as-key

- Surface narrow-required diagnostics.
- `@noalloc` rejection deferred until broader `@noalloc` enforcement
  lands (see "@noalloc Interaction" above).
- `set[Any]` / `dict[Any, V]` runtime hashable check on insert.

### Phase 9: Tests

- Unit-level cases under `tests/cases/any/`:
  - `basic_construct/` -- Any from int, str, float, None, record.
  - `print_repr_str/` -- universal ops.
  - `bool_truthy/` -- truthiness and `if`/`not`.
  - `eq_typeid/` -- equality semantics, type-mismatch False.
  - `hash_panic_non_hashable/` -- hash on non-hashable contents panics.
  - `cast_panic/` -- `typing.cast` mismatch panics with right message.
  - `auto_coerce_assign/` -- `x: int = any_var` works.
  - `auto_coerce_args/` -- function args.
  - `auto_coerce_container/` -- `list[int].append(any_var)`.
  - `isinstance_narrow_borrow/` -- narrowing into typed branch; outer Any
    survives.
  - `is_none/` -- `x is None`, `x is not None`.
  - `error_method_call/`, `error_subscript/`, `error_iter/`,
    `error_call/`, `error_binop/`, `error_is_other/` -- compile errors.
  - `nocopy_reject/` -- storing nocopy in Any rejected.
  - `dict_str_any/` -- the dynamic-config pattern (canonical use case).

## Considered and Deferred

These were considered and explicitly cut from v1. Captured here so the
rationale isn't lost.

- **Move-only contents** (`Own[T]`, `@nocopy` records). Would require
  a custom `tpy::Any` with full vtable (destroy / move slots), replacing
  the `std::any` backing. Trade-off: ~3x more codegen + runtime complexity
  for a use case (storing move-only types in a type-erased cell) that
  doesn't show up in the v1 motivating workloads. Revisit when concrete
  demand surfaces.
- **Binary-op auto-coerce** (`any + 1`, `any < 5`). Pulls `Any` into
  overload / operator resolution; turns simple expressions into runtime
  panic sites. Codex review flagged this as "high-cost sugar with ugly
  failure modes". Users can write `cast(int, any) + 1` explicitly.
- **`is` for general `Any` operands**. Byte-compare and pointer-compare
  semantics don't match Python's identity. `x is None` is the one common
  idiom worth supporting; the rest is a compile error.
- **Universal vtable dispatch for methods / attrs** (effectively making
  `Any` Python's `object`). Out of scope. Narrow first.
- **TPy-specific `expect_type` builtin** (briefly considered during the
  design discussion). The argument was that `typing.cast` is a static no-op
  in CPython, so giving it runtime semantics in TPy creates a divergence.
  Rejected on reflection: mypy/pyright cannot check casts on `Any` either
  way (cast is by design unchecked statically), so the runtime check is
  *additional* safety, not a behavior change vs. what static tools could
  catch. Auto-coerce paths produce the same divergence and we accept that.
  `panic_{name}/` test cases skip the cpy phase by convention. Reusing
  `typing.cast` keeps the surface Python-idiomatic.
- **Inheritance walk on `cast` / `isinstance`**. Defer to v2 with a
  runtime base-class table. v1 is exact-type only.

## Open Questions

1. **Borrow vs copy on auto-coerce**. Currently auto-coerce copies the
   contained value out. Could expose a borrow instead in some contexts
   (e.g., function arg of type `readonly[T]`). Skip for v1.

2. **Format spec in f-strings**. `f"{any:>10}"` errors on non-empty format
   spec. Adding spec support requires routing through `__format__` -- needs
   a richer dispatch slot. Defer.

3. **`match` / `case` on `Any`**. Pattern matching against types
   (`case int(): ...`, `case str(): ...`) is a natural extension once
   `isinstance` narrowing is in place. Likely cheap; left for a follow-up.

4. **Cross-module `AnyOps` identity under separate compilation**. `inline
   constexpr` template variables guarantee a single instance across TUs in a
   single binary. Shared library boundaries would break the invariant; out
   of scope for now (TPy builds as a single binary).


## References

- `docs/UNION_TYPES_DESIGN.md` -- isinstance narrowing infrastructure that
  `Any` reuses.
- `docs/MOVE_SEMANTICS_DESIGN.md` -- last-use auto-move; `Any` participates
  via standard rules.
- `docs/FEATURE_ROADMAP.md` (D15) -- original feature entry.
- `runtime/cpp/include/tpy/print.hpp`, `tpy/repr.hpp` -- underlying impls
  invoked by the `print` / `repr` ops slots.
