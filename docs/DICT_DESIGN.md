# Dict Type

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Core dict: `ordered_map` runtime, DictType, parser, sema, codegen, literals, subscript, `len`, `in`, `for k in d`, `print`, `get`/`pop`/`clear`, tests | Not started |
| 2 | Extended methods: `get(key, default)`, `keys()`, `values()`, `update()`, `copy()`, `setdefault()` | Not started |
| 3 | Tuple-dependent (A10): `.items()` iteration, `dict(pairs)` constructor, dict comprehensions | Not started |

---

## Overview

`dict[K, V]` is the most-used missing Python type in TurboPython. The compiler source
itself uses dicts in 24 files, making it a prerequisite for self-hosting. Beyond that,
most real-world Python programs rely on dicts for configuration, caching, lookup tables,
and data transformation.

```python
from tpy import Int32

d: dict[str, Int32] = {"x": 1, "y": 2, "z": 3}
d["w"] = 4
print(d["x"])         # 1
print(len(d))         # 4
print("x" in d)       # True

for key in d:
    print(key, d[key])
```

Maps to a custom `tpy::ordered_map<K, V>` in C++ -- a hash map that preserves
insertion order (matching Python 3.7+ dict semantics).

---

## Design Principles

1. **Python-compatible ordering**: Dicts preserve insertion order, matching CPython 3.7+.
   Iteration, printing, and equality all reflect insertion order. This is achieved via a
   custom `tpy::ordered_map` rather than `std::unordered_map`.

2. **Key type safety**: Only types with well-defined C++ hash and equality are accepted
   as keys (str, int, fixed-width ints, float, bool, Char, Enum). User records with
   `__hash__` and `__eq__` can be added later. Unhashable types (list, dict, Optional)
   are rejected at compile time.

3. **Familiar API**: Phase 1 covers the most common dict operations: literals, subscript,
   `len()`, `in`, iteration, `get()`, `pop()`, `clear()`. Less common methods follow
   in Phase 2.

4. **No deferred resolution**: Unlike list literals (which may resolve to `Array` or
   `list`), dict literals always produce `dict[K, V]`. There is no immutable dict
   variant, so no `PendingDictType` is needed.

---

## C++ Backing Type: `tpy::ordered_map<K, V>`

Python dicts preserve insertion order. `std::unordered_map` does not. Rather than
accepting this semantic difference (which would cause divergent behavior in iteration,
printing, and equality), we implement a custom ordered hash map.

### Design

`tpy::ordered_map<K, V>` combines a hash table with an intrusive doubly-linked list:

```
Hash table (std::unordered_map<K, Node*>)
    |
    v
  [bucket] -> Node { key, value, prev*, next* }
                        |
  head* <-> Node <-> Node <-> Node <-> tail*
            (1st)    (2nd)    (3rd)
```

- **Lookup**: O(1) average via the hash table
- **Insert**: O(1) -- append node to linked list tail, insert into hash table
- **Delete**: O(1) -- unlink node from list, erase from hash table
- **Iteration**: Follows the linked list (insertion order)
- **Memory**: One allocation per entry (the Node). Nodes are heap-allocated individually.

### Interface

```cpp
namespace tpy {

template<typename K, typename V>
class ordered_map {
public:
    // -- Construction --
    ordered_map() = default;
    ordered_map(std::initializer_list<std::pair<K, V>> init);
    ordered_map(const ordered_map& other);
    ordered_map(ordered_map&& other) noexcept;
    ordered_map& operator=(const ordered_map& other);
    ordered_map& operator=(ordered_map&& other) noexcept;
    ~ordered_map();

    // -- Element access --
    V& operator[](const K& key);              // insert-or-access (for d[k] = v)
    const V& at(const K& key) const;          // throws/panics on missing key

    // -- Capacity --
    int32_t size() const;
    bool empty() const;

    // -- Modifiers --
    void insert_or_assign(const K& key, V value);
    bool erase(const K& key);                 // returns true if found
    void clear();

    // -- Lookup --
    bool contains(const K& key) const;
    iterator find(const K& key);
    const_iterator find(const K& key) const;

    // -- Iteration (insertion order) --
    iterator begin();
    iterator end();
    const_iterator begin() const;
    const_iterator end() const;

    // -- Comparison --
    bool operator==(const ordered_map& other) const;
    bool operator!=(const ordered_map& other) const;

private:
    struct Node {
        K key;
        V value;
        Node* prev = nullptr;
        Node* next = nullptr;
    };

    std::unordered_map<K, Node*> table_;   // hash lookup
    Node* head_ = nullptr;                 // first inserted
    Node* tail_ = nullptr;                 // last inserted
};

}  // namespace tpy
```

The iterator dereferences to `std::pair<const K&, V&>` (or a proxy), matching
`std::unordered_map` iteration semantics so that structured bindings work:

```cpp
for (auto& [key, value] : d) { ... }
```

### Equality

Order-independent, matching Python: `{"a": 1, "b": 2} == {"b": 2, "a": 1}` is `True`.

Implementation: check same size, then verify every key-value pair in `this`
exists with the same value in `other`.

### File Location

`runtime/cpp/include/tpy/ordered_map.hpp` -- new file, included by `tpy.hpp`.

---

## Phase 1: Core Dict

### 1. Type System

**New class in `tpyc/typesys.py`**:

```python
class DictType(NamedType):
    """Dict type: dict[K, V] -> tpy::ordered_map<K, V>"""

    def __init__(self, key_type: TpyType, value_type: TpyType):
        NamedType.__init__(self, name="dict", type_args=(key_type, value_type),
                           _module_qname="builtins.dict")

    @property
    def key_type(self) -> TpyType:
        return self.type_args[0]

    @property
    def value_type(self) -> TpyType:
        return self.type_args[1]

    def to_cpp(self) -> str:
        return f"tpy::ordered_map<{self.key_type.to_cpp()}, {self.value_type.to_cpp()}>"

    def __str__(self) -> str:
        return f"dict[{self.key_type}, {self.value_type}]"

    def qualified_name(self) -> str | None:
        return "builtins.dict"

    def get_element_type(self) -> TpyType | None:
        # Subscript result type: d[k] -> V
        return self.value_type

    def get_iteration_element_type(self) -> TpyType | None:
        # For-loop variable type: for k in d -> K
        return self.key_type

    def inner_types(self) -> tuple[TpyType, ...]:
        return (self.key_type, self.value_type)

    def with_inner_types(self, types: tuple[TpyType, ...]) -> TpyType:
        return DictType(types[0], types[1])
```

**Split element type methods**: `get_element_type()` returns the value type V
(for subscript: `d[k] -> V`), while a new `get_iteration_element_type()` returns
the key type K (for `for k in d`). For all existing types (list, Array, Span, str),
`get_iteration_element_type()` defaults to `get_element_type()` (same type for both).
This avoids a dict-specific branch in subscript analysis.

The base `TpyType.get_iteration_element_type()` returns `self.get_element_type()`
by default, so only `DictType` overrides it.

### 2. Parser

**New AST node in `tpyc/parse/nodes.py`**:

```python
@dataclass
class TpyDictLiteral(TpyExpr):
    """Dict literal: {key: value, key: value, ...}"""
    keys: list[TpyExpr]
    values: list[TpyExpr]
```

**Parser changes in `tpyc/parse/parser.py`**:

1. Remove `"dict"` from `FORBIDDEN_CONSTRUCTS` (line 153)
2. Add `ast.Dict` handling in `_parse_expr()` (near line 1732, after `ast.List`):

```python
elif isinstance(node, ast.Dict):
    keys = [self._parse_expr(k) for k in node.keys]
    values = [self._parse_expr(v) for v in node.values]
    return TpyDictLiteral(keys=keys, values=values, loc=loc)
```

Note: Python's `ast.Dict` represents `{**other}` unpacking as `keys=[None]`. We
reject this in Phase 1 -- all keys must be non-None expressions.

**Type annotation support**: Dict type annotations (`dict[str, Int32]`) are already
parsed as generic type subscripts. When `"dict"` is removed from `FORBIDDEN_CONSTRUCTS`,
the existing `lookup_generic_type("dict")` path in the parser will find the registered
builtin type and create the appropriate `DictType` via the type factory.

### 3. Module Registration

**In `tpyc/modules/builtins.py`**, register the dict type alongside list:

```python
K = TypeParamRef("K", kind=TypeParamKind.TYPE)
V = TypeParamRef("V", kind=TypeParamKind.TYPE)

DICT_MUTATION_METHODS = frozenset({
    "__setitem__", "pop", "clear", "update", "setdefault",
})

module.type("dict", cpp_type="tpy::ordered_map<{K}, {V}>",
            type_params=["K", "V"],
            param_kinds=[TypeParamKind.TYPE, TypeParamKind.TYPE],
            type_factory=lambda k, v: DictType(k, v),
            methods={
    "__len__": [MethodDef(
        params=[],
        returns=INT32,
        cpp="{self}.size()",
        is_readonly=True,
    )],
    "__getitem__": [MethodDef(
        params=[ParamDef("key", K)],
        returns=V,
        cpp="tpy::dict_getitem({self}, {0})",
        is_readonly=True,
    )],
    "__setitem__": [MethodDef(
        params=[ParamDef("key", K), ParamDef("value", OwnType(V))],
        returns=VOID,
        cpp="tpy::dict_setitem({self}, {0}, {1})",
    )],
    "get": [
        # get(key) -> V | None
        MethodDef(
            params=[ParamDef("key", K)],
            returns=OptionalType(V),
            cpp="tpy::dict_get({self}, {0})",
            is_readonly=True,
        ),
        # get(key, default) -> V  (Phase 2: different return type per overload)
    ],
    "pop": [
        # pop(key) -> V  (panics on missing)
        MethodDef(
            params=[ParamDef("key", K)],
            returns=V,
            cpp="tpy::dict_pop({self}, {0})",
        ),
        # pop(key, default) -> V
        MethodDef(
            params=[ParamDef("key", K), ParamDef("default", V)],
            returns=V,
            cpp="tpy::dict_pop_default({self}, {0}, {1})",
        ),
    ],
    "clear": [MethodDef(
        params=[],
        returns=VOID,
        cpp="{self}.clear()",
    )],
}, constructors=[
    # dict() -> empty dict
    MethodDef(
        params=[],
        returns=V,  # placeholder, sema handles
        cpp="tpy::ordered_map<{K}, {V}>()",
        is_readonly=True,
    ),
])
```

### 4. Sema: Dict Literal Analysis

**In `tpyc/sema/expressions.py`**, add `_analyze_dict_literal()`:

```python
def _analyze_dict_literal(self, expr: TpyDictLiteral) -> TpyType:
    if len(expr.keys) == 0:
        raise self.ctx.error(
            "Empty dict literal requires type annotation (e.g. d: dict[str, int] = {})",
            expr,
        )

    # Analyze all keys and values
    key_types = [self.analyze_expr(k) for k in expr.keys]
    value_types = [self.analyze_expr(v) for v in expr.values]

    # All keys must have the same type
    key_type = key_types[0]
    for i, kt in enumerate(key_types[1:], 1):
        if kt != key_type:
            raise self.ctx.error(
                f"Dict key {i} has type {kt}, expected {key_type}", expr
            )

    # All values must have the same type
    value_type = value_types[0]
    for i, vt in enumerate(value_types[1:], 1):
        if vt != value_type:
            raise self.ctx.error(
                f"Dict value {i} has type {vt}, expected {value_type}", expr
            )

    # Validate key type is hashable
    _validate_dict_key_type(key_type, expr, self.ctx)

    return DictType(key_type, value_type)
```

**Key type validation**: Check that the key type is one of the allowed hashable types.

```python
_HASHABLE_TYPES = (
    Int32Type, BigIntType, FloatType, BoolType, CharType,
    StrType, StringType, StrViewType, PendingStrType,
)

def _validate_dict_key_type(key_type: TpyType, expr, ctx) -> None:
    if isinstance(key_type, _HASHABLE_TYPES):
        return
    if isinstance(key_type, FixedIntType):
        return
    if isinstance(key_type, (EnumType, IntEnumType)):
        return
    if isinstance(key_type, IntLiteralType):
        return
    raise ctx.error(
        f"Type '{key_type}' cannot be used as a dict key (not hashable)",
        expr,
    )
```

**Empty dict with annotation**: When a dict literal `{}` has a target type from an
annotation (`d: dict[str, Int32] = {}`), the empty literal should adopt that type.
This mirrors how empty list literals work -- the annotation provides the element type.

### 5. Sema: Subscript for Dict

The current `_analyze_subscript()` (expressions.py:926) requires integer index types
(line 949). Dict subscript uses non-integer keys. Add a dict-specific branch:

```python
# In _analyze_subscript, after analyzing obj_type and index_type:

# Dict subscript: d[key] -> V (key type must match K)
if isinstance(actual_type, DictType):
    if not self._types_compatible_for_dict_key(index_type, actual_type.key_type):
        raise self.ctx.error(
            f"Dict key type mismatch: expected {actual_type.key_type}, "
            f"got {index_type}",
            expr,
        )
    return actual_type.value_type

# Existing integer check (only for non-dict types):
if not isinstance(index_type, (Int32Type, BigIntType, IntLiteralType)):
    raise self.ctx.error(...)
```

This branch must come **before** the integer type check.

**Subscript assignment** (`d[k] = v`): The existing `_analyze_assign` path for
subscript targets should work via the `__setitem__` method lookup. Verify that
the subscript target analysis also handles dict (non-integer key).

### 6. Sema: `in` Operator for Dict

The `in` operator (expressions.py:434) checks that the right side is iterable and
the left operand matches the element type. Since `get_element_type()` now returns V
(the value type), dict needs a dedicated branch **before** the generic iterable check
to validate against the key type:

```python
if isinstance(right_type, DictType):
    if not types_compatible(left_type, right_type.key_type):
        raise self.ctx.error(
            f"Cannot check '{left_type}' membership in dict[{right_type.key_type}, ...]",
            expr,
        )
    return BOOL
```

### 7. Sema: For-Each Iteration

`for k in d` should iterate over keys. The sema path uses
`get_iteration_element_type()` (new method, returns K for dict) to determine the
loop variable type. The ordered_map's C++ iterator yields pairs, so codegen needs
a dict-specific path to extract just the key (see codegen section).

### 8. Codegen: Dict Literal

**In `tpyc/codegen_cpp/expressions.py`**, add `_gen_dict_literal()`:

```python
def _gen_dict_literal(self, expr: TpyDictLiteral, target_type: TpyType | None) -> str:
    dict_type = self.types.get_resolved_type(expr)
    assert isinstance(dict_type, DictType)

    cpp_key = dict_type.key_type.to_cpp()
    cpp_val = dict_type.value_type.to_cpp()

    if len(expr.keys) == 0:
        # Empty dict: needs type from annotation
        return f"tpy::ordered_map<{cpp_key}, {cpp_val}>()"

    pairs = []
    for k, v in zip(expr.keys, expr.values):
        k_cpp = self.gen_expr(k)
        v_cpp = self.gen_expr(v)
        pairs.append(f"{{{k_cpp}, {v_cpp}}}")

    return f"tpy::ordered_map<{cpp_key}, {cpp_val}>({{ {', '.join(pairs)} }})"
```

### 9. Codegen: Subscript, `in`, For-Each

**Subscript read** (`d[k]`): The existing `_gen_subscript()` uses
`get_type_method_template()` to find the `__getitem__` cpp template. This should
work via the registered method: `tpy::dict_getitem({self}, {0})`.

**Subscript write** (`d[k] = v`): Similarly uses `__setitem__` template. The key
difference from list: the `gen_index_expr()` helper (which converts BigInt to int32)
must NOT be called for dict keys. Add a dict check before index conversion.

**`in` operator** (expressions.py:374): Currently generates `std::find()` for
non-string collections. Add a dict-specific branch:

```python
if isinstance(right_resolved, DictType):
    find_expr = f"({right}.contains({left}))"
```

This is more efficient than `std::find` and semantically correct (checks keys only).

**For-each iteration**: The ordered_map iterator yields pairs. For `for k in d`, we
need to extract just the key. Add a dict branch in `_gen_for_each()`:

```python
if isinstance(iterable_type, DictType):
    iterable = self.expressions.gen_expr_deref(stmt.iterable)
    cpp_var = escape_cpp_name(stmt.var)
    key_type = iterable_type.key_type
    cpp_type = key_type.to_cpp()
    self.ctx.temps.flush(out, indent)
    # Extract key from pair using structured binding
    out.write(f"{indent}for (auto& [__k, __v] : {iterable}) {{\n")
    out.write(f"{indent}    {cpp_type} {cpp_var} = __k;\n")
    self.ctx.var_types[stmt.var] = key_type
    self._gen_loop_body(out, stmt, indent, key_type)
    return
```

Alternative: use `std::views::keys` from C++23:

```python
out.write(f"{indent}for ({cpp_type} {cpp_var} : {iterable} | std::views::keys) {{\n")
```

This is cleaner and avoids the extra variable declaration.

### 10. Codegen: `print(d)`

**Runtime `DictPrinter`** in `printing.hpp` (or `dict_ops.hpp`):

```cpp
template<typename K, typename V>
struct DictPrinter {
    const tpy::ordered_map<K, V>& value;
    explicit DictPrinter(const tpy::ordered_map<K, V>& v) : value(v) {}
};

template<typename K, typename V>
std::ostream& operator<<(std::ostream& os, const DictPrinter<K, V>& p) {
    os << '{';
    bool first = true;
    for (auto& [k, v] : p.value) {
        if (!first) os << ", ";
        first = false;
        detail::print_element(os, k);
        os << ": ";
        detail::print_element(os, v);
    }
    os << '}';
    return os;
}
```

**Codegen** (builtins.py `gen_print()`): Detect `DictType` and emit
`tpy::DictPrinter(expr)`.

### 11. Runtime: `dict_ops.hpp`

New file `runtime/cpp/include/tpy/dict_ops.hpp`:

```cpp
#pragma once

#include "ordered_map.hpp"
#include "core.hpp"
#include <optional>

namespace tpy {

// d[key] -- panics on missing key (KeyError)
template<typename K, typename V>
const V& dict_getitem(const ordered_map<K, V>& m, const K& key) {
    auto it = m.find(key);
    if (it == m.end()) {
        tpy_panic("KeyError");
    }
    return (*it).second;
}

// Mutable version for augmented assignment (d[k] += v)
template<typename K, typename V>
V& dict_getitem(ordered_map<K, V>& m, const K& key) {
    auto it = m.find(key);
    if (it == m.end()) {
        tpy_panic("KeyError");
    }
    return (*it).second;
}

// d[key] = value (insert or update)
template<typename K, typename V>
void dict_setitem(ordered_map<K, V>& m, const K& key, V value) {
    m.insert_or_assign(key, std::move(value));
}

// d.get(key) -> Optional[V]
template<typename K, typename V>
std::optional<V> dict_get(const ordered_map<K, V>& m, const K& key) {
    auto it = m.find(key);
    if (it == m.end()) return std::nullopt;
    return (*it).second;
}

// d.pop(key) -> V (panics on missing)
template<typename K, typename V>
V dict_pop(ordered_map<K, V>& m, const K& key) {
    auto it = m.find(key);
    if (it == m.end()) {
        tpy_panic("KeyError");
    }
    V result = std::move((*it).second);
    m.erase(key);
    return result;
}

// d.pop(key, default) -> V
template<typename K, typename V>
V dict_pop_default(ordered_map<K, V>& m, const K& key, const V& def) {
    auto it = m.find(key);
    if (it == m.end()) return def;
    V result = std::move((*it).second);
    m.erase(key);
    return result;
}

// __len__ overload
template<typename K, typename V>
int32_t __len__(const ordered_map<K, V>& m) {
    return m.size();
}

// __bool__ overload
template<typename K, typename V>
bool __bool__(const ordered_map<K, V>& m) {
    return !m.empty();
}

}  // namespace tpy
```

### 12. Runtime: `BigInt` Hash

`BigInt` needs a `std::hash` specialization for use as a dict key. Add to `bigint.hpp`:

```cpp
namespace std {
template<>
struct hash<tpy::BigInt> {
    size_t operator()(const tpy::BigInt& b) const noexcept {
        // Use the internal representation for hashing
        if (b.is_small()) {
            return std::hash<int64_t>{}(b.small_value());
        }
        // For large values, hash the limbs
        size_t h = 0;
        for (auto limb : b.limbs()) {
            h ^= std::hash<uint32_t>{}(limb) + 0x9e3779b9 + (h << 6) + (h >> 2);
        }
        return h;
    }
};
}
```

If `BigInt`'s internal API doesn't expose `is_small()` / `small_value()` / `limbs()`,
a simpler fallback is hashing `b.to_string()`. The exact implementation depends on
what BigInt exposes -- the key point is that a `std::hash<BigInt>` must exist.

Enum types already use `enum class` which has a default `std::hash` via the underlying
integer type.

### 13. CPython Stubs

Dict is native to Python, so no CPython stubs are needed for the dict type itself.
However, test files using TPy types (Int32, etc.) as values already have stubs in
`lib/cpy/tpy/`. No new CPython stubs required for dict.

### 14. Key Type Validation

**Allowed key types** (Phase 1):

| Type | C++ `std::hash` | Notes |
|------|-----------------|-------|
| `str` / `String` / `StrView` | `std::hash<std::string>` / `std::hash<std::string_view>` | Works out of the box |
| `int` (BigInt) | `std::hash<BigInt>` | Needs new specialization |
| `Int8`..`Int64`, `UInt8`..`UInt64` | `std::hash<intN_t>` | Works out of the box |
| `float` | `std::hash<double>` | Works out of the box |
| `bool` | `std::hash<bool>` | Works out of the box |
| `Char` | `std::hash<char>` | Works out of the box |
| `Enum` / `IntEnum` | `std::hash<underlying_int>` | Works via `enum class` |
| `IntLiteralType` | Resolves to concrete type | Validated after resolution |

**Rejected key types**:

| Type | Reason |
|------|--------|
| `list[T]` | Mutable, not hashable in Python |
| `dict[K, V]` | Mutable, not hashable in Python |
| `Optional[T]` | No natural hash for None+T combination |
| `Ptr[T]` / `ReadOnlyPtr[T]` | Pointer identity is fragile |
| User records | Need `__hash__` + `__eq__` (future) |

### 15. Tests

Create test cases under `tests/cases/dict/`:

| Test | What it covers |
|------|---------------|
| `dict_basic` | Dict literal, subscript read/write, len, print |
| `dict_methods` | `get(key)`, `pop()` (both overloads), `clear()` |
| `dict_in` | `key in d`, `key not in d` |
| `dict_iteration` | `for k in d` iterates keys in insertion order |
| `dict_empty` | Empty dict with annotation: `d: dict[str, Int32] = {}` |
| `dict_constructor` | `dict[str, Int32]()` empty dict via constructor |
| `dict_int_keys` | Int32 and BigInt as keys |
| `dict_overwrite` | `d[k] = v` overwrites existing key, preserves order |
| `dict_bool_truthiness` | Empty dict is falsy, non-empty is truthy |
| `dict_param_return` | Dict as function parameter and return type |
| `error_dict_empty_no_annotation` | `d = {}` without type annotation |
| `error_dict_key_type` | List or other unhashable type as key |
| `error_dict_key_mismatch` | Mixed key types in literal |
| `error_dict_value_mismatch` | Mixed value types in literal |
| `error_dict_subscript_type` | Wrong key type in subscript |
| `panic_dict_missing_key` | `d[missing_key]` panics |
| `panic_dict_pop_missing` | `d.pop(missing_key)` panics |

All tests should be CPython-compatible (no `no_cpython.txt`).

---

## Phase 2: Extended Methods

Scope: `get(key, default)`, `keys()`, `values()`, `update()`, `copy()`, `setdefault()`.

### `get(key, default)`

The two-arg overload `get(key, default) -> V` returns a different type than the
one-arg overload `get(key) -> V | None`. This kind of return-type-varying overload
is not currently supported for user types. Deferred to Phase 2 to design the
overload resolution properly.

### `keys()` and `values()`

Return `list[K]` and `list[V]` respectively (allocating copies). This is simpler
than returning lazy views and matches the most common usage pattern (iterating once).

```python
d = {"a": 1, "b": 2}
ks: list[str] = d.keys()       # ["a", "b"]
vs: list[Int32] = d.values()   # [1, 2]
```

C++ implementation: iterate the ordered_map and collect into a `std::vector`.

A future optimization could return lazy views (`std::views::keys` / `std::views::values`),
but this requires new view types in the type system.

### `update()`

```python
d.update(other)    # merge other dict into d
```

C++ implementation: iterate other, `insert_or_assign` each pair into self.

### `copy()`

```python
d2 = d.copy()    # shallow copy
```

C++ implementation: copy constructor.

### `setdefault()`

```python
v = d.setdefault("key", 0)    # insert if missing, return value
```

C++ implementation: `find` + conditional `insert_or_assign`, return reference.

---

## Phase 3: Tuple-Dependent Features

**Blocked by Tuple type (A10).**

### `.items()` Iteration

```python
for k, v in d.items():
    print(k, v)
```

Returns an iterable of `tuple[K, V]`. Requires:
- Tuple type in the type system
- Tuple unpacking in for-loop (`for k, v in ...`)
- `.items()` method returning `list[tuple[K, V]]` or a lazy view

### `dict(pairs)` Constructor

```python
d = dict([(k, v) for k, v in pairs])
```

Requires iterable of tuples.

### Dict Comprehension

```python
d = {k: v for k, v in items}
```

Requires comprehension expression support (separate feature).

---

## Future Extensions

- **Lazy views for `keys()`/`values()`**: Return `std::views::keys` / `std::views::values`
  wrappers instead of allocating `list[K]`/`list[V]`. Needs new view types in the type system.
- **User records as keys**: Allow user-defined types with `__hash__` + `__eq__` as dict keys.
  Needs `Hashable` protocol in the type system.
- **`del d[k]`**: Requires `del` statement support (separate feature E3).

---

## Dependencies

**Blocked by**: Nothing for Phase 1. Phase 3 blocked by Tuple (A10).

**Unlocks**:
- Real-world programs using dicts
- Self-hosting (24 files in compiler source use dict)
- Enum `__members__` mapping
- Many stdlib patterns (counters, grouping, caching)

---

## Implementation Map

### New Files

| File | Purpose |
|------|---------|
| `runtime/cpp/include/tpy/ordered_map.hpp` | `tpy::ordered_map<K,V>` implementation |
| `runtime/cpp/include/tpy/dict_ops.hpp` | Dict runtime helpers + DictPrinter |

### Modified Files

| File | Changes |
|------|---------|
| `tpyc/typesys.py` | Add `DictType` class |
| `tpyc/parse/nodes.py` | Add `TpyDictLiteral` AST node |
| `tpyc/parse/__init__.py` | Export `TpyDictLiteral` |
| `tpyc/parse/parser.py` | Remove "dict" from FORBIDDEN_CONSTRUCTS, parse `ast.Dict` |
| `tpyc/modules/builtins.py` | Register dict type with methods and constructors |
| `tpyc/sema/expressions.py` | Dict literal analysis, subscript with non-integer key, `in` operator |
| `tpyc/sema/statements.py` | Dict subscript assignment |
| `tpyc/sema/list_literals.py` | Recognize DictType in `is_type_iterable()` |
| `tpyc/codegen_cpp/expressions.py` | Dict literal codegen, `in` operator for dict, subscript |
| `tpyc/codegen_cpp/statements.py` | Dict for-each iteration, subscript assignment |
| `tpyc/codegen_cpp/builtins.py` | `print(d)` with DictPrinter |
| `tpyc/codegen_cpp/types.py` | Handle DictType in type resolution |
| `runtime/cpp/include/tpy/tpy.hpp` | Include `ordered_map.hpp` and `dict_ops.hpp` |
| `runtime/cpp/include/tpy/bigint.hpp` | Add `std::hash<BigInt>` specialization |
| `docs/LANGUAGE_FEATURES.md` | Document dict[K,V] support |

---

## Notes

- **Augmented assignment on subscript**: `d[k] += 1` uses the mutable `dict_getitem()`
  overload (returns `V&`), so it compiles as `tpy::dict_getitem(d, k) += 1`. Panics
  on missing key, matching Python's `KeyError`.

- **Cross-module dict**: Works via standard header/source split. `ordered_map` is
  header-only.

- **Generic functions with dict**: `def f(d: dict[str, T]) -> T` should work via
  existing generic inference. Needs testing during implementation.
