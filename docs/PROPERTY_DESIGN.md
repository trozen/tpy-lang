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
| 7 | A READ is its getter call from sema on -- one node kind, no consumer keys on it | Done |

Phase 7's measure is the PROPERTY-KEYED CONSUMER SITE: a place in sema, THIR
lowering or codegen that has to ask whether an expression or a callee is a
property. **45 before, 37 after.** What is left is the two things that stay
property-specific by design -- the declaration-side conventions on the getter's
`FunctionInfo` (the storage-ref return spelling, the `set_` rename, the
`@auto_readonly` const twin) and the WRITE position -- plus the interim arms
listed in TODO.md's chain-hop entry, which are admitted for a getter and
refused for the spelled twin for want of a call arm rather than a rule. Count
distinct SITES, not mentions: a `grep` over the predicate names is a larger and
unrelated number, because the predicates are named in prose wherever the rule
is explained.

### Known Limitations

| Limitation | Notes |
|------------|-------|
| No narrowing on property access | A read IS its getter call, so `if w.num is not None:` does not narrow `w.num` below it and the spelled-method twin does not either -- nothing kills the fact when what backs the getter changes. Workaround: bind once, `n = w.num; if n is not None: use(n)`, which narrows the local. TODO.md, "Narrowing over a pure zero-arg getter" |
| Borrow tracker key mismatch | `v = b.items` borrows `"b.items"` but `b._items = [...]` mutates `"b._items"` -- different keys, no warning. Fix: property-to-field alias registration |
| No augmented assignment | `obj.prop += 1` rejected. Could desugar to `obj.prop = obj.prop + 1` (double getter evaluation) |
| Bound off a loop-local ALIAS receiver | `for i in range(3): b = bs[i]; holder = b.<getter>` is a located error, *"reference to 'b' may outlive its storage; use copy(b) for an independent value"*. What `b` names IS function-scope storage, so the binding would be sound -- but `check_escape`'s hoistability test reads the LOCAL's block depth, not the aliased storage's, and refuses to hoist a name that is not an rvalue binding. This is the escape analysis's own limit and NOT a property one: the field spelling `b = bs[i]; holder = b.m` answers with the identical message at the identical line, and both spellings answer that way before and after this unit -- the intended symmetry, measured rather than assumed. Workarounds that compile: `for b in bs:`, whose loop variable inherits the container's depth, and `holder = copy(b.<getter>)` for an independent value. `b = copy(bs[i])` also compiles now, with hoisted backing and the same rebind warning as a constructor initializer. The rvalue form `b = B(i)` is a different cell and is not rejected: it warns and hoists, like its field twin |
| A getter as the augmented-assignment RECEIVER | `b.rec.x += 1` is rejected (`augassign.recv.accessor_double_eval`). The lvalue is reached THROUGH the read, and the aug-assign render spells the target on both sides, so the getter would run twice where Python runs it once. Evaluation order is a postponed language decision, so the accessor spelling is held rather than fixed alone; the spelled-method twin `b.rec_m().x += 1` still double-evaluates. Workaround: `r = b.rec` then `r.x += 1`. `BUGS.md#augassign-call-receiver-double-eval` |
| Four positions reject a STORAGE-REF getter | A getter returning a pointer-repr `Optional[T]` hands back the field's `std::optional<T>` BY REFERENCE, which no plain method can return, so `print(s.o)`, `s.o.bump()`, `s.o.x` and a ternary arm over one are located rejects rather than renders -- the lift each position needs was never written. No wrong code is emitted -- each position stops before spelling a signature that does not exist. Tags: `method.fi_kind` / `method.ret_type` at the receiver and the field-through, `expr.ifexpr:ifexpr.getter_arm_const_source` at a ternary arm, and `expr.storage_ref_getter` as the fallback for any OTHER position (no position reaches it today: every one probed either renders -- a decl, a call argument, a container element, an f-string -- or stops at an earlier gate with its own tag; it stays so a position that moves onto this family is named rather than silent). The render each wants is the FIELD spelling's over the getter call, blocked on `BUGS.md#getter-source-const-not-tracked`. Workaround: bind first, `v = s.o` then use `v`. TODO.md, "Four positions REJECT a storage-ref `@property` read" |
| No `@x.deleter` | Rarely used |
| No properties in protocols | Protocol fields (`class P(Protocol): x: int32`) serve a similar purpose |
| No `@override` on properties | Properties are inherited but not overridable |
| Optional[non-value] narrowing | Pre-existing: `if obj.node is not None: obj.node.val` generates invalid C++ for non-value Optional. Affects both fields and properties |
| `str` / `BigInt` return by value | General method return convention -- not property-specific. Use `-> StrView` for zero-copy str |
| Read off a TEMPORARY receiver: every BINDING position | `v = mk().items`, and the same at a call argument, a `return`, a `with` manager, a `match` subject, a field write, a `d[k] = ...` value, a module global or frame slot, a for-each or comprehension iterable, a ternary arm and the constructor member-init list, are located rejects (`<sink>.lends_from_temporary`). What the getter lends can OUTLIVE the receiver -- a global, a longer-lived object -- and CPython aliases it, so owning a copy loses every later mutation and binding a reference dangles. TRANSIENT reads stay admitted: `print(len(mk().items))`, `mk().items[0]`, `f"{mk().name}"`, a `+=` source, a dict key, a membership needle. Workaround: bind the receiver first, `h = mk()` then `v = h.items`. The spelled-method twin keeps its conceded warn-and-emit tier (`BUGS.md#readonly-borrow-of-temporary-receiver`). Pinned by the `records/error_property_off_temporary_receiver*` family, whose `_arg` header carries the tag table |
| An ARGUMENT asks the CALLEE, not the position | The lend is admitted at an argument only where the callee provably READS it during the call: `len`, `sum`, `any`, `sorted`, `str()`, `list()` and a user function taking a view admit; `reversed`, `enumerate`, `zip`, `map`, `filter`, `itertools.islice` and every generator or coro factory RETAIN what they are given and reject. Sema owns the fact per parameter (`return_borrows_from`, `addr_escapes_params`); a body-less `@native` / `@cpp_template` stub returning an `Iterator` is stamped at registration as retaining its iterable arguments, any other stub has no per-parameter fact and answers from its declared signature, whose two gaps are `BUGS.md#native-stub-declares-no-param-retention`. Pinned by `records/error_property_off_temporary_receiver_retaining_builtin` |
| Aggregate ELEMENT slot, even off a NAMED receiver | `t = (b.rec, 1)`, `[b.rec for i in range(1)]`, a dict key or value, a set element: a getter whose read renders as a C++ lvalue REFERENCE has no element render, because the slot must pick between STORAGE (a copy, where CPython aliases) and BORROW (a `Rec&` element, which no C++ container can hold). A VIEW getter is not affected when the element slot OWNS -- `xs: list[str] = [b.name]` renders `{std::string(b.name())}`, as the field spelling and the method twin do. The unannotated `xs = [b.name]` infers `list[StrView]` and rejects like every other source at a view-typed element slot (`BUGS.md#view-element-slot-admits-no-source`). Pinned by `records/error_property_elem_lends_storage` |
| Computed pointer-repr Optional getter | The Phase-5 "storage type" getter-return convention (row above) holds only when the body returns a stored optional FIELD; a computed `-> Rec \| None` (returning a plain field or `None`) emits uncompilable C++. Toolchain-caught. See the BUGS.md `@property` pointer-repr `Optional` entry |

### A read off a TEMPORARY receiver: the sink table

One fact (`lends_from_dying_source`) and one verdict per POSITION. `SinkForm.
DYING_SOURCE_LEND` has a row per sink in `_POS_FORMS`
(`tpyc/thir/lower/context.py`), and no sink that BINDS or HOLDS its value past
the full expression admits it -- so this is the table of sinks, not a list of
positions someone remembered. The tag names the sink, and each row is pinned by
a case under `tests/cases/records/`:

| sink | tag | case suffix |
|------|-----|-------------|
| local decl | `local_decl.lends_from_temporary` | (the base case) |
| branch-first decl | `local_decl.lends_from_temporary` | `_hoisted_decl` |
| free-call argument | `call.arg_shape.container` (the arg-shape gate speaks first for this shape) | `_arg` |
| stub-method argument | `arg.lends_from_temporary` | `_method_arg` |
| retaining builtin | `arg.lends_from_temporary` | `_retaining_builtin` |
| for-each / comprehension | `foreach.iter_lends_from_temporary` | `_foreach`, `_comp` |
| field store | `assign.field_write_lends_from_temporary` | `error_property_view_store_temporary` |
| setitem value | `setitem_value.lends_from_temporary` | `_setitem` |
| `with` manager | `with_manager.lends_from_temporary` | `_with` |
| return | `return.lends_from_temporary` | `_return` |
| match subject | `match_subject.lends_from_temporary` | `_match` |
| global slot | `global_slot_write.lends_from_temporary` | `_global` |
| frame slot | `frame_slot_write.lends_from_temporary` | `_frame` |

One label to know about: an ANNOTATED module global (`SLOT: list[int32] =
mk().items`) takes the global-slot row, while an INFERRED one (`v = mk().name`)
reports `local_decl.lends_from_temporary` -- a module-level binding without an
annotation shares the LOCAL decl lowering. Both reject, correctly; the split is
in the module-level decl routing and predates this rule.

Three more sinks refuse the lend by construction but are reached through a
different tag, because a shape gate answers first: a ternary arm at
`ifexpr.getter_arm_const_source`, a walrus at `expr.walrus`, and the constructor
member-init list at `mil_init.lends_from_temporary`. An aggregate ELEMENT slot
is refused one step earlier and by a WIDER rule
(`container_elem.accessor_lends_storage`, which holds off a NAMED receiver too).

An ARGUMENT is the one position that asks the CALLEE rather than the sink; see
the Known Limitations row above.

THE METHOD TWIN: the fact is the accessor spelling's only, so wherever the twin
compiles it keeps its conceded warn-and-emit tier
(`BUGS.md#readonly-borrow-of-temporary-receiver`). Two positions refuse both
spellings, and neither for this rule: the free-call argument and the stub-method
argument stop at the argument-SHAPE gate first. The whole matrix of
accessor-vs-twin verdicts is the committed table beside
`scripts/thir_migration/review/property_position_sweep.py`.

WORKAROUND at every row: bind the receiver first -- `h = mk()` then
`mutate(h.items)`.

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
    _radius: float64

    def __init__(self, radius: float64) -> None:
        self._radius = radius

    @property
    def radius(self) -> float64:
        return self._radius

    @radius.setter
    def radius(self, value: float64) -> None:
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

**A property READ is a method call, at every position.** `_try_find_field`
checks `protocols.lookup_record_property()` after fields and records the
getter on the node; `analyze_field_access` then turns the node INTO that
`TpyMethodCall` in place (`become_method_call`), at the end of the read
analysis. Identity is preserved, so the recorded type, the loc and the
field-path narrowing facts survive. Nothing downstream keys on a property's
node kind: every position answers at its call arm.

The WRITE position is the one exception -- a settable property on the left of
an assignment keeps the field-access node kind, pinned by `is_write_target`
before the target is analysed as a read, so the setter is looked up by the
field name the node still carries.

- Assignment analysis constructs setter `TpyMethodCall`, stores on `property_setter_call`
- Property methods removed from `RecordInfo.methods` (not callable as `obj.prop()`)
- Stored in `RecordInfo.properties` dict as `PropertyInfo(getter, setter)`
- Augmented assignment on properties rejected with clear error

### Codegen

- A read needs no delegation: lowering sees a method call
- `_gen_assign_code`: if `property_setter_call` is set, delegates to `_gen_method_call`
- Getter return type: normal `_resolve_return_type` except for Optional/Union
  pointer-repr types which use storage type (`std::optional<T>&`, `::tpy::Union<A,B>&`).
  This is the one property-specific CONVENTION that survives the collapse
  (`property_getter_returns_storage_ref`), and the rows that consume it key on
  the getter's `FunctionInfo`, never on a node kind: a plain method returning
  the same types hands back the borrow form (`T*`, `::tpy::Union<A*, B*>`)
- `@readonly` free functions: `const=True` passed to `_resolve_return_type`

## C++ Output

### Value-type property (int32, float64, bool, char)

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
