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
| A getter as the augmented-assignment RECEIVER or INDEX | Works since 2026-10-19: `b.rec.x += 1` and `xs[b.i] += 1` run the getter once -- the statement holds its result in a temp and reads and writes back through it (`{ auto&& __tmp_1 = b.rec(); __tmp_1.x = ::tpy::add_check<int32_t>(__tmp_1.x, 1); }`), as it does for the spelled-method twin `b.rec_m().x += 1` (`TpyAugAssign.hold_parts`). |
| Four positions reject a STORAGE-REF getter | A getter returning a pointer-repr `Optional[T]` hands back the field's `std::optional<T>` BY REFERENCE, which no plain method can return, so `print(s.o)`, `s.o.bump()`, `s.o.x` and a ternary arm over one are located rejects rather than renders -- the lift each position needs was never written. No wrong code is emitted -- each position stops before spelling a signature that does not exist. Tags: `method.fi_kind` / `method.ret_type` at the receiver and the field-through, `expr.ifexpr:ifexpr.getter_arm_const_source` at a ternary arm, and `expr.storage_ref_getter` as the fallback for any OTHER position (no position reaches it today: every one probed either renders -- a decl, a call argument, a container element, an f-string -- or stops at an earlier gate with its own tag; it stays so a position that moves onto this family is named rather than silent). The render each wants is the FIELD spelling's over the getter call, blocked on `BUGS.md#getter-source-const-not-tracked`. Workaround: bind first, `v = s.o` then use `v`. TODO.md, "Four positions REJECT a storage-ref `@property` read" |
| No `@x.deleter` | Rarely used |
| No properties in protocols | Protocol fields (`class P(Protocol): x: int32`) serve a similar purpose |
| No `@override` on properties | Properties are inherited but not overridable |
| Optional[non-value] narrowing | Pre-existing: `if obj.node is not None: obj.node.val` generates invalid C++ for non-value Optional. Affects both fields and properties |
| `str` / `BigInt` return by value | General method return convention -- not property-specific. Use `-> StrView` for zero-copy str |
| Read off a TEMPORARY receiver: the method twin's tier | `v = mk().items`, a field or element read through it that still points into it (`z = mk().ch.sub`), an argument a bodied callee hands back (`r = keep(mk().rec)`) and an operand a user dunder returns (`y = b + mk().ch.rec`) compile with sema's *Result borrows from temporary ...* warning and dangle when the name is read after the statement -- exactly as the spelled method `mk().get_items()` does (`BUGS.md#readonly-borrow-of-temporary-receiver`). A getter lending a global aliases it as CPython does; an owning position (a field write, an `Own[T]` return or yield) copies with the copy warning. A few positions keep the borrow with NO diagnostic, in both spellings: a `Ptr` taken of a field read through the getter, a stub / native callee keeping the argument, a module global slot (`BUGS.md#getter-off-temporary-kept-silently`), for MIR to warn. No lifetime refusal is specific to the getter; the two that stay apply to every spelling: a chained-comparison operand that is not a free-to-copy scalar (`unspecified.lends_from_temporary`), and a view argument a callee keeps a copy of (`arg.lends_from_temporary`, row below). Workaround: bind the receiver first, `h = mk()` then `v = h.items`. Pinned by `records/property_off_temporary` (`part_warn` warned; the other parts safe and copying positions) |
| An ARGUMENT asks the CALLEE, not the position | A reference-returning getter read off a temporary is a borrow, as its bound twin `h = mk(); h.items` is, and an argument takes it as the twin does: `acc += mk().data`, `acc.extend(mk().data)` and `list(mk().data)` copy the elements with the twin's warning, `"-".join(mk().names)`, `ss.update(mk().s)` and `len(mk().items)` read it in place. Whether the callee keeps an argument is one verdict (`callee_retains_arg`, `tpyc/sema/call_lends.py`): a callee with a body by what sema measured (`return_borrows_from`, `addr_escapes_params`), a body-less binding by its signature -- its result, and the copies it may keep of a value holding a pointer (docs/NATIVE_INTEROP.md, "What a binding keeps of its arguments"). A callee that keeps it (`reversed`, `enumerate`, a generator factory, `list[StrView].extend`) takes the borrow as the method twin does; in a compound statement's head the statement keeps the receiver (`for x in reversed(mk().items)` is safe). The one refusal left at an argument is not about getters: a VIEW a callee keeps a copy of may not view a temporary's storage (`sink.stash(mk().text)`, `arg.lends_from_temporary`, `records/error_native_stash_view_of_temporary`). The TPy-body gap is `BUGS.md#native-stub-declares-no-param-retention`. Pinned by `records/stub_arg_off_temporary` and `records/property_off_temporary` |
| Aggregate ELEMENT slot, even off a NAMED receiver | `t = (b.rec, 1)`, `[b.rec for i in range(1)]`, a dict key or value, a set element: a getter whose read renders as a C++ lvalue REFERENCE has no element render, because the slot must pick between STORAGE (a copy, where CPython aliases) and BORROW (a `Rec&` element, which no C++ container can hold). A VIEW getter is not affected when the element slot OWNS -- `xs: list[str] = [b.name]` renders `{std::string(b.name())}`, as the field spelling and the method twin do. The unannotated `xs = [b.name]` infers `list[StrView]` and rejects like every other source at a view-typed element slot (`BUGS.md#view-element-slot-admits-no-source`). Pinned by `records/error_property_elem_lends_storage` |
| Computed pointer-repr Optional getter | The Phase-5 "storage type" getter-return convention (row above) holds only when the body returns a stored optional FIELD; a computed `-> Rec \| None` (returning a plain field or `None`) emits uncompilable C++. Toolchain-caught. See the BUGS.md `@property` pointer-repr `Optional` entry |

### A read off a TEMPORARY receiver: no getter lifetime refusal

The lowering has no lifetime refusal specific to a getter read off a temporary
receiver: the property spelling renders what its method twin renders, and the
lifetime is warned -- by sema's *Result borrows from temporary* warning today,
by MIR as its coverage grows. Two lifetime refusals stay, and they apply to
every spelling, not to getters: a chained-comparison operand that is not a
free-to-copy scalar (row below), and a view a callee keeps a copy of viewing a
temporary's storage (`arg.lends_from_temporary`, the kept view argument).
`lends_from_dying_source`
(`tpyc/value_category.py`) still states the fact (a `match` subject's place
root reads it); `SinkForm.TRANSIENT` in `_POS_FORMS`
(`tpyc/thir/lower/context.py`) marks the positions that finish inside the full
expression, which a select's slot hoist reads.

| position | render | diagnostic | case |
|------|-----|-----|-----|
| local decl | `std::vector<int32_t>& p = mk().items();` | dangle warning (none when the getter lends a global) | `property_off_temporary` (`part_warn`, `main`) |
| branch-first decl | `std::vector<int32_t>* v; ... v = &(mk().items());` | dangle warning (none when the getter lends a global) | `property_off_temporary` (`part_warn`, `main`) |
| field / element read through the read | `Rec& z = mk().ch().rec;` | dangle warning | `property_off_temporary` (`part_warn`) |
| argument a bodied callee hands back | `Rec& r = keep(mk().ch().rec);` | dangle warning | `property_off_temporary` (`part_warn`) |
| operand a dunder hands back | `const Rec& y = ((b) + (mk().ch().rec));` | dangle warning | `property_off_temporary` (`part_warn`) |
| chained-comparison operand (not a free-to-copy scalar) | refused, every spelling (method, field, select) | `unspecified.lends_from_temporary` | `error_property_off_temporary_chained_compare`, `error_select_off_temporary_chained_compare` |
| return | `return mk().items();` | none when the getter lends a global; sema refuses a borrow of `self` (`Cannot return local or temporary as reference`) | `property_off_temporary` |
| module global slot | `SLOT = &(mk().items());` | none (silent with a self-lending getter: `BUGS.md#getter-off-temporary-kept-silently`) | `property_off_temporary` |
| field write, `Own[T]` return / yield | a copy | copy warning | `property_off_temporary` |
| for-each / comprehension source, `with` manager, `match` subject | the head keeps the receiver | none (safe) | `property_off_temporary`, `calls/fresh_arg_head_keep` (`part_head_receivers`) |
| generator expression passed to a call that finishes in the expression (`sum(x for x in mk().items)`) | the source is read before the statement ends | none (safe, transient) | `property_off_temporary` (`part_loops`) |
| loop head over a NESTED generator expression (`for i, x in enumerate(x for x in mk().items)`) | `auto __src_0 = ::tpy::builtin_enumerate(__genexpr_main_1(mk().items()));` -- the head keeps nothing | none (silent, `BUGS.md#getter-off-temporary-kept-silently`) | -- |
| `Ptr` of a field read through the read, a stub / native callee keeping it | `const V* p = &mk().ch().vsub;` | none (`BUGS.md#getter-off-temporary-kept-silently`) | -- |

Positions still refused by a SHAPE gate the bound twin shares, not by a
lifetime rule: a setitem value (`setitem.container_value_shape`, ...:
BUGS.md#getter-result-slot-shape-rejects;
`records/error_property_off_temporary_receiver_setitem`), a generator frame
slot (`res.alias_bind`; `records/error_property_off_temporary_receiver_frame`), a ternary arm (`ifexpr.getter_arm_const_source`,
where the method twin compiles: BUGS.md#getter-source-const-not-tracked), a
walrus (`expr.walrus`). An aggregate ELEMENT slot is refused by a WIDER rule
(`container_elem.accessor_lends_storage`, which holds off a NAMED receiver too).

A free-call argument at a parameter the callee does not retain binds the
getter's reference in place (`grow(mk().items)` passes `mk().items()`, and the
callee's write reaches what the getter lent) -- `calls/argument_sink`, section
`property_temp_receiver`. The whole matrix of accessor-vs-twin verdicts is the
committed table beside
`scripts/thir_migration/review/property_position_sweep.py`.

WORKAROUND for a warned dangle: bind the receiver first -- `h = mk()` then
`v = h.items`.

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
before the target is analysed as a read. The read analysis still resolves the
property through the receiver's deref chain (`Ptr[T]`, a `__deref__` wrapper)
and records it, accessors substituted at the receiver's type arguments, as
`resolved_property`; the setter comes from there, not from a second lookup on
the raw receiver type.

- Assignment analysis builds the setter `TpyMethodCall` with `method_call_through`
  (the receiver markers a getter call carries) and stores it on `property_setter_call`
- Property methods removed from `RecordInfo.methods` (not callable as `obj.prop()`)
- Stored in `RecordInfo.properties` dict as a `PropertyInfo` whose `accessors`
  hold every accessor body's own `FunctionInfo` (each getter clone, then the
  setter) -- the home of each body's analysis facts; `getter` (the first live
  getter, the one calls bind) and `setter` are derived from it
- Augmented assignment on properties rejected with clear error

### Codegen

- A read needs no delegation: lowering sees a method call
- The assignment lowering (`tpyc/thir/lower/statements.py`): if `property_setter_call` is set, the statement lowers that call through the method-call path in discard position
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
