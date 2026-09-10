# Property Design

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | `@property` getter (value + non-value types, auto_readonly cloning) | Done |
| 2 | `@x.setter` (Own param wrapping, setter call via TpyMethodCall) | Done |
| 3 | Inheritance (child reads parent property) | Done |
| 4 | Validation (read-only error, @staticmethod conflict, duplicate setter, name conflicts) | Done |
| 5 | Optional/Union pointer-repr getter return (storage type) | Done |
| 6 | `@readonly` free function const return type | Done |

### Known Limitations

| Limitation | Notes |
|------------|-------|
| No narrowing on property access | Property access is a method call -- could return different values. Same as function calls. Workaround: `v = obj.prop; if v is not None: use(v)` |
| Borrow tracker key mismatch | `v = b.items` borrows `"b.items"` but `b._items = [...]` mutates `"b._items"` -- different keys, no warning. Fix: property-to-field alias registration |
| No augmented assignment | `obj.prop += 1` rejected. Could desugar to `obj.prop = obj.prop + 1` (double getter evaluation) |
| No `@x.deleter` | Rarely used |
| No properties in protocols | Protocol fields (`class P(Protocol): x: Int32`) serve a similar purpose |
| No `@override` on properties | Properties are inherited but not overridable |
| Optional[non-value] narrowing | Pre-existing: `if obj.node is not None: obj.node.val` generates invalid C++ for non-value Optional. Affects both fields and properties |
| `str` / `BigInt` return by value | General method return convention -- not property-specific. Use `-> StrView` for zero-copy str |
| Computed pointer-repr Optional getter | The Phase-5 "storage type" getter-return convention (row above) holds only when the body returns a stored optional FIELD; a computed `-> Rec \| None` (returning a plain field or `None`) emits uncompilable C++. Toolchain-caught. See the BUGS.md `@property` pointer-repr `Optional` entry |

### Future Extensions

| Feature | Notes |
|---------|-------|
| Property narrowing for simple getters | Analyze getter body to detect `return self._field` pattern; map property to backing field for safe narrowing |
| Augmented assignment | Desugar `obj.prop += val` to `obj.prop = obj.prop + val` |
| Protocol properties | `@property` on Protocol methods with conformance checking |
| Getter body inlining | For simple `return self._field` getters, inline field access instead of method call -- eliminates call overhead |
| `@x.deleter` | Python's property deleter protocol |
| Borrow tracker aliases | Register property-to-field mapping so backing field mutations invalidate property borrows |

---

## Overview

`@property` support lets classes expose computed attributes with field-access syntax.
Getters and setters are compiled as normal C++ methods -- the compiler transforms
`obj.prop` into a getter call and `obj.prop = val` into a setter call.

```python
class Circle:
    _radius: Float64

    def __init__(self, radius: Float64) -> None:
        self._radius = radius

    @property
    def radius(self) -> Float64:
        return self._radius

    @radius.setter
    def radius(self, value: Float64) -> None:
        self._radius = value
```

## Design Principles

**Properties are normal methods.** Getter/setter definitions go through standard
`gen_params`, `gen_body`, `_gen_method_overload` -- no custom codegen for method
bodies. This ensures argument resolution, borrowing, lifetimes, const deduction,
and all other method infrastructure apply automatically.

**Minimal special-casing.** The only codegen overrides are:
- Getter return type for Optional/Union pointer-repr types (return storage type
  `std::optional<T>&` instead of method convention `T*`)
- `in_property_getter` flag to skip pointer-repr return conversion in getter body
- Setter call site delegates to `_gen_method_call` via synthesized `TpyMethodCall`

## Compilation Pipeline

### Parser

- `@property` resolved alongside `@staticmethod` in `_resolve_decorator`
- `@x.setter` detected before general decorator resolution by matching
  `ast.Attribute(value=Name(id=known_property), attr='setter')`
- Property getter marked `auto_readonly=True` for const/mutable overload cloning
- Setter value param wrapped in `Own` for non-value types (ownership transfer)
- `_auto_declare_fields_from_init` skips property names to avoid field conflicts

### Sema

- `_try_find_field` checks `protocols.lookup_record_property()` after fields
- Constructs `TpyMethodCall` node for getter, stores on `TpyFieldAccess.property_getter_call`
- Assignment analysis constructs setter `TpyMethodCall`, stores on `property_setter_call`
- Property methods removed from `RecordInfo.methods` (not callable as `obj.prop()`)
- Stored in `RecordInfo.properties` dict as `PropertyInfo(getter, setter)`
- Augmented assignment on properties rejected with clear error

### Codegen

- `_gen_field_access`: if `property_getter_call` is set, delegates to `_gen_method_call`
- `_gen_assign_code`: if `property_setter_call` is set, delegates to `_gen_method_call`
- Getter return type: normal `_resolve_return_type` except for Optional/Union
  pointer-repr types which use storage type (`std::optional<T>&`, `::tpy::Union<A,B>&`)
- `@readonly` free functions: `const=True` passed to `_resolve_return_type`

## C++ Output

### Value-type property (Int32, Float64, bool, Char)

Single const overload, return by value:

```cpp
int32_t radius() const { return this->_radius; }
void set_radius(int32_t value) { this->_radius = value; }
```

### Non-value-type property (records, list, dict)

Dual overloads for mutable/const reference semantics:

```cpp
Point& start() { return this->_start; }
const Point& start() const { return this->_start; }
void set_start(Point&& p) { this->_start = std::move(p); }
```

### str property

Returns `std::string` by value (same as any method returning `str`).
Use `-> StrView` for zero-copy access if desired.

### Optional/Union non-value property

Getter returns reference to storage type, not pointer-repr:

```cpp
std::optional<Node>& node() { return this->_node; }
const std::optional<Node>& node() const { return this->_node; }
void set_node(std::optional<Node>&& n) { this->_node = std::move(n); }
```
