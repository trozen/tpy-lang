# Dict Type

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Core dict: `ordered_map` runtime, DictType, parser, sema, codegen, literals, subscript, `len`, `in`, `for k in d`, `print`, `get`/`pop`/`clear`, tests | Done |
| 2 | Extended methods: `get(key, default)`, `keys()`, `values()`, `update()`, `copy()`, `setdefault()`, `del d[k]` | Not started |
| 3 | Tuple-dependent: `.items()` iteration, `dict(pairs)` constructor, dict comprehensions | Not started |

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
   as keys (str, fixed-width ints, float, bool, Char, Enum). User records with
   `__hash__` and `__eq__` can be added later. Unhashable types (list, dict, Optional)
   are rejected at compile time.

3. **Familiar API**: Phase 1 covers the most common dict operations: literals, subscript,
   `len()`, `in`, iteration, `get()`, `pop()`, `clear()`. Less common methods follow
   in Phase 2.

4. **No deferred resolution**: Unlike list literals (which may resolve to `Array` or
   `list`), dict literals always produce `dict[K, V]`. There is no immutable dict
   variant, so no `PendingDictType` is needed.

5. **Dunder protocol**: Dict operations use the same `tpy::__getitem__` / `tpy::__setitem__`
   / `tpy::__len__` / `tpy::__bool__` overload pattern as list (defined in `dunder.hpp`).
   Dict-specific methods (`get`, `pop`) live in `dict_ops.hpp`.

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

### Two Iterator Types

The map has two iterator families, matching Python's dict semantics:

- **Key iterator** (default `begin()`/`end()`): Yields `const K&`. Used by `for k in d`,
  making C++ range-for work generically with no special codegen.
- **Items iterator** (`items_begin()`/`items_end()`): Yields proxy `pair<const K&, V&>`.
  Used internally by `find()`, `DictPrinter`, and runtime helpers. Will be exposed to
  user code via `.items()` in Phase 3.

This split means `for k in d` uses the same generic range-for codegen path as lists
and other containers -- no dict-specific branch in the codegen.

### KeyArg Template Pattern

Dict runtime functions use a separate `KeyArg` template parameter (deduced from the
argument) to accept key-compatible types without requiring exact match. For example,
`std::string_view` can be passed where `K=std::string`, with `K(key)` performing
explicit conversion:

```cpp
template<typename K, typename V, typename KeyArg>
V dict_pop(ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(K(key));  // explicit conversion
    ...
}
```

### Equality

Order-independent, matching Python: `{"a": 1, "b": 2} == {"b": 2, "a": 1}` is `True`.

Implementation: check same size, then verify every key-value pair in `this`
exists with the same value in `other`.

### File Location

`runtime/cpp/include/tpy/ordered_map.hpp` -- new file, included by `tpy.hpp`.

---

## Phase 1: Core Dict (Done)

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

    def get_element_type(self) -> TpyType | None:
        return self.value_type       # d[k] -> V

    def get_iteration_element_type(self) -> TpyType | None:
        return self.key_type         # for k in d -> K
```

**Split element type methods**: `get_element_type()` returns V (for subscript),
`get_iteration_element_type()` returns K (for iteration). The base `TpyType`
defaults `get_iteration_element_type()` to `get_element_type()`, so only `DictType`
overrides it. No dict-specific branches needed in the generic for-each codegen.

### 2. Parser

**New AST node** `TpyDictLiteral` in `tpyc/parse/nodes.py` with `keys` and `values`
lists. `ast.Dict` handling added in `parser.py`. `"dict"` removed from
`FORBIDDEN_CONSTRUCTS`.

### 3. Module Registration

**In `tpyc/modules/builtins.py`**, dict is registered with methods that use the
standard dunder protocol:

- `__getitem__`: `tpy::__getitem__({self}, {0})` (overload in `dunder.hpp`)
- `__setitem__`: `tpy::__setitem__({self}, {0}, {1})` (overload in `dunder.hpp`)
- `__len__`: `tpy::__len__({self})` (overload in `dunder.hpp`)
- `get`: `tpy::dict_get({self}, {0})` (in `dict_ops.hpp`)
- `pop`: `tpy::dict_pop({self}, {0})` / `tpy::dict_pop_default({self}, {0}, {1})`
- `clear`: `{self}.clear()`

### 4. Sema

**Dict literal analysis** (`_analyze_dict_literal`):
- Unifies key and value types across all entries
- IntLiteralType is resolved using annotation context when available (via
  `analyze_expr_with_hint`), falling back to `default_int_for_literal`
- Validates key type is hashable

**Annotation hint propagation**: When a dict literal has an annotation context
(e.g. `d: dict[str, int] = {"a": 1}`), the expected key/value types are passed
to `_analyze_dict_literal`, which uses `analyze_expr_with_hint` to analyze keys
and values. This allows IntLiteralType to resolve to the annotated type (e.g.
BigInt) instead of the default (Int32). Works for nested dicts too.

**Subscript**: Dict branch before integer index check. Key type validated via
`check_type_compatible`.

**`in` operator**: Dict branch checks against `key_type` and generates
`d.contains(key)`.

**Dangling return check**: `TpyDictLiteral` recognized as a temporary in
`is_dangling_return()`, giving a proper sema error when returning a dict literal
without `Own[]`.

**DictType compatibility**: Uses recursive `check_type_compatible` on key and
value types (not exact match), so `dict[str, Int32]` is compatible with
`dict[str, Int32]` even through coercion paths.

### 5. Codegen

**Dict literal**: `tpy::ordered_map<K, V>({{k1, v1}, {k2, v2}, ...})`

**Subscript**: Uses registered `__getitem__`/`__setitem__` templates (same path
as list).

**`in` operator**: `({right}.contains({left}))` -- more efficient than `std::find`.

**For-each iteration**: No dict-specific codegen. The generic range-for path uses
`get_iteration_element_type()` to determine the loop variable type (K for dict).
The key iterator makes `for (K k : d)` work directly.

**Print**: `tpy::DictPrinter(expr)` using explicit `items_begin()`/`items_end()`
iteration.

### 6. Runtime

**`dunder.hpp`**: `__getitem__`, `__setitem__`, `__len__`, `__bool__` overloads
for `ordered_map`.

**`dict_ops.hpp`**: Dict-specific methods not covered by dunder protocol:
`dict_get` (returns `std::optional<V>`), `dict_pop`, `dict_pop_default`,
`DictPrinter`.

### 7. Key Type Validation

**Allowed key types**:

| Type | C++ `std::hash` | Notes |
|------|-----------------|-------|
| `str` / `String` / `StrView` | `std::hash<std::string>` / `std::hash<std::string_view>` | Works out of the box |
| `Int8`..`Int64`, `UInt8`..`UInt64` | `std::hash<intN_t>` | Works out of the box |
| `float` | `std::hash<double>` | Works out of the box |
| `bool` | `std::hash<bool>` | Works out of the box |
| `Char` | `std::hash<char>` | Works out of the box |
| `Enum` / `IntEnum` | `std::hash<underlying_int>` | Works via `enum class` |

**Not yet supported as keys**:

| Type | Reason |
|------|--------|
| `int` (BigInt) | Sema accepts it, but `std::hash<BigInt>` is not implemented -- C++ compilation fails |
| User records | Need `__hash__` + `__eq__` (future) |

**Rejected key types** (compile-time error):

| Type | Reason |
|------|--------|
| `list[T]` | Mutable, not hashable in Python |
| `dict[K, V]` | Mutable, not hashable in Python |
| `Optional[T]` | No natural hash for None+T combination |
| `Ptr[T]` / `ReadOnlyPtr[T]` | Pointer identity is fragile |

### 8. Tests

21 test cases under `tests/cases/dict/`:

| Test | What it covers |
|------|---------------|
| `dict_basic` | Dict literal, subscript read/write, len, print |
| `dict_methods` | `get(key)`, `pop()` (both overloads), `clear()` |
| `dict_in` | `key in d`, `key not in d` |
| `dict_iteration` | `for k in d` iterates keys in insertion order |
| `dict_empty` | Empty dict with annotation: `d: dict[str, Int32] = {}` |
| `dict_int_keys` | Int32 as keys |
| `dict_int_annotation` | `dict[str, int]` annotation with bare int literals |
| `dict_literal_ints` | Dict literal with bare integer literals (Int32 inference) |
| `dict_overwrite` | `d[k] = v` overwrites existing key, preserves order |
| `dict_param_return` | Dict as function parameter and return type |
| `dict_mutate_param` | Dict mutation through function parameter |
| `dict_nested` | Nested `dict[str, dict[str, Int32]]` with annotation propagation |
| `error_dict_empty_no_annotation` | `d = {}` without type annotation |
| `error_dict_key_type` | List or other unhashable type as key |
| `error_dict_key_mismatch` | Mixed key types in literal |
| `error_dict_value_mismatch` | Mixed value types in literal |
| `error_dict_subscript_key_type` | Wrong key type in subscript |
| `error_dict_in_key_type` | Wrong key type in `in` operator |
| `error_dict_return_dangling` | Returning dict literal without `Own[]` |
| `panic_dict_missing_key` | `d[missing_key]` panics |
| `panic_dict_pop_missing` | `d.pop(missing_key)` panics |

---

## Phase 2: Extended Methods

Scope: `get(key, default)`, `keys()`, `values()`, `update()`, `copy()`,
`setdefault()`, `del d[k]`.

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

### `del d[k]`

```python
del d["key"]    # remove key-value pair
```

Requires `del` statement support (separate feature E3). C++ implementation:
`m.erase(key)`.

---

## Phase 3: Tuple-Dependent Features

Tuple type (A10) is now implemented. This phase is unblocked.

### `.items()` Iteration

```python
for k, v in d.items():
    print(k, v)
```

Returns an iterable of `tuple[K, V]`. Requires:
- Tuple unpacking in for-loop (`for k, v in ...`)
- `.items()` method using the existing `items_begin()`/`items_end()` iterators

The `items_iterator` already yields proxy `pair<const K&, V&>` which can map
to `tuple[K, V]`.

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

- **BigInt keys**: Add `std::hash<BigInt>` specialization to `bigint.hpp`. Sema already
  accepts BigInt as a key type, but C++ compilation fails without the hash.
- **Lazy views for `keys()`/`values()`**: Return `std::views::keys` / `std::views::values`
  wrappers instead of allocating `list[K]`/`list[V]`. Needs new view types in the type system.
- **User records as keys**: Allow user-defined types with `__hash__` + `__eq__` as dict keys.
  Needs `Hashable` protocol in the type system.
- **Avoid string key copy in for-each**: `for k in d` where K=str generates
  `for (std::string k : d)`, copying each key. The key iterator yields `const K&`, so
  `const auto&` would avoid the copy, but this affects all for-each loops (not just dict)
  and breaks cases where the loop body takes a mutable reference to the element.
  See `TODO.md` for details.

---

## Dependencies

**Blocked by**: Nothing for Phase 1. Phase 3 unblocked (Tuple A10 is implemented).

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
| `runtime/cpp/include/tpy/ordered_map.hpp` | `tpy::ordered_map<K,V>` with key and items iterators |
| `runtime/cpp/include/tpy/dict_ops.hpp` | `dict_get`, `dict_pop`, `dict_pop_default`, `DictPrinter` |

### Modified Files

| File | Changes |
|------|---------|
| `tpyc/typesys.py` | `DictType` class, `get_iteration_element_type()` on base `TpyType` |
| `tpyc/parse/nodes.py` | `TpyDictLiteral` AST node |
| `tpyc/parse/__init__.py` | Export `TpyDictLiteral` |
| `tpyc/parse/parser.py` | Remove "dict" from FORBIDDEN_CONSTRUCTS, parse `ast.Dict` |
| `tpyc/modules/builtins.py` | Register dict type with dunder methods and constructors |
| `tpyc/sema/expressions.py` | Dict literal analysis, annotation hint propagation, subscript, `in` operator, key validation |
| `tpyc/sema/statements.py` | Empty dict with annotation, dict subscript assignment |
| `tpyc/sema/compatibility.py` | `TpyDictLiteral` in dangling return check, DictType recursive compatibility |
| `tpyc/sema/list_literals.py` | Use `get_iteration_element_type()` in fallback path |
| `tpyc/codegen_cpp/expressions.py` | Dict literal codegen, `in` operator (`contains`), subscript |
| `tpyc/codegen_cpp/statements.py` | Use `get_iteration_element_type()` for for-each element type |
| `tpyc/codegen_cpp/builtins.py` | `print(d)` with `DictPrinter` |
| `runtime/cpp/include/tpy/dunder.hpp` | `__getitem__`, `__setitem__`, `__len__`, `__bool__` overloads for `ordered_map` |
| `runtime/cpp/include/tpy/tpy.hpp` | Include `ordered_map.hpp` and `dict_ops.hpp` |
| `docs/LANGUAGE_FEATURES.md` | Document dict[K,V] support |

---

## Notes

- **Augmented assignment on subscript**: `d[k] += 1` uses the mutable `__getitem__`
  overload (returns `V&`), so it compiles as `tpy::__getitem__(d, k) += 1`. Panics
  on missing key, matching Python's `KeyError`.

- **Cross-module dict**: Works via standard header/source split. `ordered_map` is
  header-only.

- **Generic functions with dict**: `def f(d: dict[str, T]) -> T` works via existing
  generic inference.

- **`operator[]` on ordered_map**: Provided for standalone C++ use (inserts `V{}` on
  missing key, like `std::map`). Not used by generated code -- codegen uses the dunder
  `__getitem__` which panics on missing key (Python semantics).
