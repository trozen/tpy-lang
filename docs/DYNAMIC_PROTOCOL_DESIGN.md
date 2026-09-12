# Dynamic Protocol Dispatch -- Design

Extracted from `PROTOCOL_DESIGN.md` section 12.

## Progress

| Step | Description | Status |
|------|-------------|--------|
| 1 | `@dynamic` decorator, parser + sema + object-safety validation | Done |
| 2 | Abstract base + adapter codegen (base class + `tpy::Adapter`, `tpy::RefAdapter`) | Done |
| 3 | Protocol-typed locals (stack slot + pointer-local) | Done |
| 4 | Protocol-typed function params (`Base&`, call-site dispatch) | Done |
| 5 | Direct C++ inheritance (`class Dog(Pet)` -> `struct Dog : Pet`) | Done. An override's params are emitted at the base pure-virtual's **declared** const-ness (`protocols._dynamic_param_list`), not the override's own const-inference: a method overriding a `@dynamic` virtual passes `mutated_params=None` to the param emitter (`_gen_method_overload`, gated on the existing `override` flag) so an unmutated reference-type param (`dict`/`list`/class) renders non-const to match the base -- otherwise const-inference would emit `const T*` against the base's `T*` and the override would not bind the virtual slot, leaving the class abstract. Test: `dyn_inherit_ref_param`. |
| 5a | Conditional/loop reassignment (hoisted `std::optional` slots) | Done |
| 6 | Return types (provably long-lived values only) | Done |
| 7 | `@dynamic` protocol params in record methods/constructors | Done for the bare and `readonly` forms. **Not** for `Optional[Pet]`: as of 2026-09-09 a method or constructor param spelled `s: Pet | None` is still rejected (the Phase 8 pointer-repr allowance is threaded from the free-function registration path only) -- `BUGS.md#opt-dyn-protocol-method-param`. |
| 8 | `Optional[Pet]` sema rejection | Done, then **narrowed to non-parameter positions**. A direct-`@dynamic`-protocol `Optional[Pet]` is now allowed at a FREE-FUNCTION parameter position (a record-method or constructor param of the same spelling is still rejected -- `BUGS.md#opt-dyn-protocol-method-param`) -- it lowers to a `[const] Pet*` borrow (the @dynamic-protocol param codegen emits pointer-repr for the nullable case instead of dropping the Optional to `Pet&`), `is None` narrows, and `isinstance(p, Sub)` dispatches via `dynamic_cast` on the pointer. It is also allowed as an **init-only function-local** (Phase 2): `p: Optional[Pet] = Dog()` lowers to the same `const Pet*` pointing at the materialized rvalue; the **rvalue-rebind** position stays rejected (the shared slot can't be retyped per rvalue). The rejection stays for storage / return / field / container / owned / global positions and no-init locals, where the abstract base has no value-repr (`validate_type`'s `allow_pointer_repr_dynamic` flag, set at the param call site and at init-only local var-decls, gates it; nested recursion resets the flag so `list[Optional[Pet]]` etc. stay rejected). Tests: `opt_dyn_protocol_param`, `opt_dyn_generic_protocol_param`, `opt_dyn_protocol_local`, `error_opt_dyn_protocol_local_rebind`, `error_protocol_dynamic_optional_field`. **Adjacent rejection:** `Own[Optional[Polymorphic]]` (a *concrete* polymorphic class wrapped in Own-Optional) is rejected for the same family of reasons -- the Own-Optional slot is laid out for the base alone, so a derived instance would slice and `isinstance` has no valid lowering. Implemented at `tpyc/sema/type_ops.py::validate_type` covering every position uniformly (params, return types, fields, method params + returns, and any nested position the validator recurses into, e.g. tuple elements); see `tests/cases/protocols/error_own_optional_polymorphic/` and `tests/cases/protocols/error_tuple_own_optional_polymorphic/`. Broader "polymorphic + wrappers + isinstance" landscape (the long-term lifting of this rejection) tracked in TODO.md. |
| 9 | Protocol field access through erased type | Future |
| 10 | Cross-module `@dynamic` protocols | Done |
| 11 | `@dynamic` extending `@dynamic` (base class inheritance chain) | Done |
| 12 | Generic `@dynamic` protocols | Done |
| 13 | `Box[P]` integration (heap-allocated dynamic values) | Done |
| 14 | Record fields typed as `@dynamic` protocol via `Box[P]` | Done |
| 15 | `list[Box[P]]` heterogeneous containers | Done |
| 16 | `Own[P]` as a plain function parameter + return type (with method access on the owned value) | Done. Param/return lower to `std::unique_ptr<P>`; `p.method()` body access emits `p->method()`; concrete-source returns wrap via `std::make_unique<Adapter<P, T>>(...)` (or `std::make_unique<T>(...)` for inheritance conformers). Forward `Own[P] -> Own[P]` returns rely on C++ implicit-move (no `std::move` wrap). |
| 17 | `Rc[P]` for `@dynamic` P -- shared-ownership erased dyn protocol | Done (single-allocation shape). One heap block per `Rc.new`: a generic `_RcCell[U]` derived from `@dynamic _RcCellBase`, holding strong/weak counters and an inline `UninitArrayStorage[U, 1]` payload. The factory is bounded -- `Rc.new[U: T](value: Own[U]) -> Own[Rc[T]]` -- and its body coerces `Ptr[U] -> Ptr[T]` via `cell.storage.ptr()`. Polymorphic destruction routes through the cell's fused virtual `release_strong` (strong-zero -> drop payload -> decrement collective weak in one vcall), so the inline `U` payload is destroyed via its dynamic-type destructor before the block is freed. Conformer selection at the call site: an inheritance conformer (`Parrot : Pet`) keeps `U=Parrot` (`Rc<Pet>::new_<Parrot>(...)`, free `Parrot*->Pet*` upcast); a structural conformer (`Cat`, no inheritance) has codegen substitute the C++ method-level template arg `U -> Adapter<Pet, Cat>` (`Rc<Pet>::new_<Adapter<Pet, Cat>>(...)`) so the inlined cell payload is a `Pet`-derived type -- sema still infers `U=Cat`, the substitution is a codegen-only decision driven by `representational_subst_params`. Net cost: one `operator new` per `Rc.new` (down from two), matching `std::make_shared`. |
| 18 | Transitive virtual-override propagation through `@dynamic`-rooted inheritance chains | Done. A class inheriting a `@dynamic` protocol via a concrete-class intermediate (e.g. `Throwable -> BaseExc -> ValErr`) now emits `override` on method redefinitions at every level, not just one hop. `_get_dynamic_override_info`, `_dynamic_proto_requires_nonconst`, and `_check_method_hiding` all walk the MRO via `iter_ancestor_records` when consulting `implemented_protocols`. The "method hides ancestor" warning is suppressed for methods that are now proper transitive overrides. |
| 19 | `Optional[ConcreteRoot]` / bare `ConcreteRoot` / `self` class dispatch via `isinstance` + `dynamic_cast` | Done. For `E` a *concrete class* that transitively inherits a `@dynamic` protocol (e.g. `class BaseExc(Throwable)`), `isinstance(p, Subclass)` lowers to a runtime `dynamic_cast` on `Optional[E]` parameters (lowered to `T*`), bare `E` parameters (lowered to `T&`), and `self` inside an instance method on `E` (lowered to `this` -- already a pointer). The cast input picks the right shape per binding: bare name for pointer-shaped bindings (Optional params, pointer globals, pointer-locals, imports), `&name` for reference params, `this` for `self`. Codegen materializes rvalue temps at the actual class type (no slicing) for arg-passing and init-only locals; tuple form ORs casts. Activated for the built-in `BaseException` tree via a `Throwable` `@dynamic` protocol declared in `tpy._core._types` and inherited by `BaseException`. **Subclass-typed narrowing** in the isinstance true branch is supported: codegen emits `Sub& __var = *dynamic_cast<Sub*>(<src>);` once at branch entry (cast-and-cache); subsequent reads route through the typed reference, enabling subclass-only field access like `if isinstance(e, OsErr): e.code`. The C++17 if-init form shares the cast between the bool check and the body (`if (Sub* __p_ptr = dynamic_cast<Sub*>(<src>); __p_ptr) { ... }`) so the runtime check happens exactly once. Const-ness of the cast matches the source's readonly status (or the method's const-ness for `self`). **Early-return narrowing:** `if not isinstance(p, Sub): return` (or `raise`) extracts the cast at the outer scope so post-guard reads route through the narrowed reference, mirroring the assert/if-body shape; covered by `tests/cases/protocols/isinstance_early_return_narrowing/`. The same surface is *not* yet available for direct `Optional[Pet]` (see Phase 8 note). Owned storage / rvalue return positions are addressed in Phase 20. |
| 20 | Polymorphic owned storage via `Box[@dynamic Root]`; slicing-site sema rejection; `raise <expr>` -> `__raise__` desugar; built-in exception hierarchy moved to pure TPy | Partial -- Stage 4c (pure-TPy hierarchy) deferred; see [TODO.md](../TODO.md) "Phase 20 follow-up" entry for the four design walls. **Shipped:** `Throwable` activated as `@native + @dynamic` (runtime-provided abstract base at `::tpy::Throwable`); `Box[Throwable]` storage with auto-deref through Box's `__deref__`; codegen auto-emits `clone()` / `__raise__()` / `what()` on every concrete Throwable subclass (user redeclaration of any of the three is rejected at sema to prevent C++ redefinition collisions); slicing-site rejection at `tpyc/sema/compatibility.py::_check_polymorphic_slicing` keyed on conversion shape (borrow-into-owned-polymorphic-slot), with carve-outs for `Own[T]` moves, fresh-rvalue constructor calls, `None`, and *fresh-ctor locals* (a non-reassigned local whose sole binding is an exact-type constructor call -- tracked as `FunctionContext.current_fresh_ctor_locals`, so `c = Dog(); c.x = 1; Box(c)` is allowed since `c`'s dynamic type is provably `Dog`); sema rules enforce BaseException-inheritance and copy-constructibility on Throwable implementers (qname-keyed against `tpy.Throwable` / `builtins.BaseException`); `raise <expr>` desugars to `<expr>.<deref-chain>.__raise__()` with `TpyRaise.deref_depth` set by sema's peel-loop, while `raise X(args)` keeps the idiomatic `throw X(args)` form via a fresh-construction peephole (mechanically equivalent for static-type-known construction); runtime `raise<E>(...)` template replaced by per-class `raise_X(msg)` helpers in `core.hpp`. **Deferred to Stage 4c:** moving the 17 native exception classes from `core.hpp`/`async.hpp` to pure TPy, retiring the `TPY_THROWABLE_VIRTUALS` macro, and relocating the `raise_X` helpers from inline-in-`core.hpp` to forward-decls in `throwable.hpp` + definitions in `lib/tpy/tpy/_builtins/_raise.py`. See [EXCEPTION_DESIGN.md](EXCEPTION_DESIGN.md) E9 for the exception-side details. |

## Overview

Static protocols (C++20 concepts) are zero-cost but monomorphized -- each concrete type
produces a separate template instantiation. Dynamic protocols add runtime dispatch via
vtables, enabling polymorphism where the concrete type is erased at compile time.

## Design Principles

- **No hidden allocations** -- dynamic dispatch uses stack-allocated adapters and the
  existing pointer-local/slot mechanism. Heap allocation only happens when the user
  explicitly requests it (e.g., `Box[P]`, `list[Box[P]]`).
- **Structural conformance** -- a type conforms to a `@dynamic` protocol if it has the
  required methods. No explicit `extends` declaration needed (same as non-dynamic protocols).
- **Pythonic syntax** -- `pet: Pet = Dog()` just works. No wrapper types required.

## `@dynamic` Annotation

A protocol must be explicitly marked `@dynamic` to enable runtime dispatch:

```python
from typing import Protocol
from tpy import dynamic

@dynamic
class Speakable(Protocol):
    def speak(self) -> None: ...

@dynamic
class Drawable(Protocol):
    def draw(self, x: int32, y: int32) -> None: ...
```

Without `@dynamic`, a protocol is always statically dispatched (C++20 concept, template
monomorphization). This is the existing behavior, unchanged.

`@dynamic` means "this protocol supports vtable dispatch." It enables using the protocol
as a type annotation for variables and parameters with runtime polymorphism.

## Dispatch Modes

A `@dynamic` protocol supports both static and dynamic dispatch, selected by syntax:

| Usage | Dispatch | C++ |
|-------|----------|-----|
| `pet: Speakable` (param) | dynamic | `void f(Speakable& pet)` |
| `pet: Speakable` (local) | dynamic | `Speakable* pet` (pointer-local) |
| `T: Speakable` (type bound) | static | `template<__Speakable_Concept__ T> void f(T& pet)` |

- **Bare protocol type** (`pet: Speakable`) -- dynamic dispatch via vtable. Works for
  function parameters (passed as `Base&`) and local variables (pointer-local to
  stack-allocated adapter).
- **Type bound** (`T: Speakable`) -- static dispatch via C++20 concept constraint,
  monomorphized. Same as non-dynamic protocols. Use this when you want zero-cost
  dispatch and don't need type erasure.

For non-`@dynamic` protocols, `pet: Proto` remains static (template), same as today.

## Usage Examples

```python
@dynamic
class Pet(Protocol):
    def make_noise(self) -> None: ...

class Dog:
    def make_noise(self) -> None:
        print("Woof")

class Cat:
    def make_noise(self) -> None:
        print("Meow")

# --- Dynamic dispatch (type-erased, vtable) ---

def greet(pet: Pet) -> None:
    pet.make_noise()         # virtual dispatch

dog: Pet = Dog()             # stack-allocated adapter, pointer-local
dog.make_noise()             # virtual dispatch
greet(dog)                   # pass erased value

my_dog = Dog()
greet(my_dog)                # implicit wrap: temporary adapter at call site

# Reassignment to different concrete type
pet: Pet = Dog()
pet = Cat()                  # rebind pointer to new adapter slot
pet.make_noise()             # "Meow"

# --- Static dispatch (monomorphized, zero-cost) ---

def greet_fast[T: Pet](pet: T) -> None:
    pet.make_noise()         # direct call, no vtable
```

## Zero-Allocation Stack Dispatch

Dynamic protocol variables use the existing pointer-local and slot hoisting
infrastructure. No heap allocation occurs.

**Local variable** -- when Dog explicitly inherits Pet, the slot is a plain Dog (direct
inheritance from the base class). Otherwise an owning adapter wraps the value:

```python
pet: Pet = Dog()       # Dog(Pet) -> direct inheritance
pet.make_noise()
```

Generated C++ (direct inheritance):

```cpp
Dog __slot_1{Dog()};
Pet* pet = &__slot_1;
pet->make_noise();   // virtual dispatch
```

Generated C++ (structural conformance, no explicit inheritance):

```cpp
tpy::Adapter<Pet, Parrot> __slot_1{Parrot()};
Pet* pet = &__slot_1;
pet->make_noise();   // virtual dispatch via adapter
```

**Reassignment** creates additional slots at each reassignment site:

```python
pet: Pet = Dog()
pet = Cat()
pet.make_noise()
```

Generated C++:

```cpp
Dog __slot_1{Dog()};
Pet* pet = &__slot_1;
Cat __slot_2{Cat()};
pet = &__slot_2;
pet->make_noise();  // dispatches to Cat
```

**Conditional/loop reassignment** hoists slots to function scope via `std::optional`
so they outlive the block:

```python
pet: Pet = Dog()
if cond:
    pet = Cat()        # slot must survive the if-block
pet.make_noise()
```

Generated C++:

```cpp
std::optional<Cat> __slot_2;                // hoisted to function scope
Dog __slot_1{Dog()};
Pet* pet = &__slot_1;
if (cond) {
    __slot_2.emplace(Cat());
    pet = &*__slot_2;
}
pet->make_noise();
```

**Function parameters** -- the compiler selects the optimal call-site mechanism:

```python
def greet(pet: Pet) -> None:
    pet.make_noise()

greet(my_dog)     # Dog inherits Pet -> implicit upcast, zero cost
greet(parrot)     # structural conformance lvalue -> RefAdapter (zero-copy)
greet(Parrot())   # structural conformance rvalue -> owning Adapter
greet(erased_pet) # already-erased Pet -> dereference pointer-local
```

Generated C++:

```cpp
void greet(Pet& pet) {
    pet.make_noise();
}

// Direct inheritor (lvalue): implicit upcast to Base&
greet(my_dog);

// Structural conformance (lvalue): ref adapter, zero-copy, mutations visible
tpy::RefAdapter<Pet, Parrot> __tmp_1{parrot};
greet(__tmp_1);

// Structural conformance (rvalue): owning adapter
tpy::Adapter<Pet, Parrot> __tmp_2{Parrot()};
greet(__tmp_2);

// Already erased: dereference pointer-local
greet(*erased_pet);
```

## Return Types

Returning a `@dynamic` protocol type is allowed when the value provably outlives the
caller -- i.e., when it refers to a global or a parameter (not a locally-constructed
value, since the stack adapter would be destroyed):

```python
global_dog: Pet = Dog()

def get_global_pet() -> Pet:
    return global_dog          # OK: global outlives caller

def echo_pet(pet: Pet) -> Pet:
    return pet                 # OK: parameter outlives caller

def make_pet() -> Pet:
    return Dog()               # ERROR: local adapter destroyed on return
```

For returning locally-constructed dynamic values, use explicit heap allocation:

```python
def make_pet() -> Box[Pet]:
    return Box(Dog())          # OK: heap-allocated, caller owns
```

## What Requires Explicit Wrapping

Dynamic protocol types cannot be used directly in contexts that require owning storage
with unknown lifetime. These require explicit `Box[P]` or `Rc[P]`:

| Context | Direct `Pet` | `Ptr[Pet]` (non-owning) | `Box[Pet]` (owning) | `Optional[BaseConcrete]` * |
|---------|-------------|-------------------------|---------------------|--------------------------|
| Local variable | OK (stack adapter) | OK (`Pet*`, nullable) | OK (heap) | OK init-only (slot retyped to rvalue's class); rebind rejected |
| Function param | OK (reference) | OK (`Pet*`, nullable) | OK | OK (`const BaseConcrete*` borrow, no-slice temp materialization) |
| Return value | Only if source outlives caller | Only if pointee outlives caller | OK | Only if source outlives caller; rvalue construction returned is rejected |
| Record field | No (size unknown) | OK (`Pet*`, caller manages lifetime) | OK | No (storage form has same lifetime/slicing issue) -- use `Optional[Box[BaseConcrete]]` |
| `list[Pet]` | No (elements need ownership) | OK (`std::vector<Pet*>`) | `list[Box[Pet]]` | -- use `list[Box[BaseConcrete]]` |
| `isinstance(x, Sub)` | Runtime `dynamic_cast` (`Sub` must inherit `Pet`) | Runtime `dynamic_cast` on the `P*` | Compile-time check on `Box`'s inner | Runtime `dynamic_cast` (Phase 19) |

`Ptr[P]` for a `@dynamic` P is the natural non-owning sibling of `Box[P]`
(owning, heap-allocated) and `Rc[P]` (shared-owning, single heap block:
refcount cell with inline payload). It is a
raw `P*` pointer that dispatches `P`'s methods via the vtable; the caller
is responsible for keeping the pointee alive. Construct via the usual
`Ptr[T]` paths -- `take_ptr(concrete)` and direct
record-to-`Ptr[P]` coercion both work for any concrete class
implementing `P`; `box.get()` etc. also produce a `Ptr[P]` when the box's
inner type implements `P`. The `Ptr[Subclass] -> Ptr[@dynamic P]` and
`Subclass -> Ptr[@dynamic P]` coercions are documented in
`docs/LANGUAGE_FEATURES.md`'s pointer-coercion table (the
`take_ptr(subclass) -> Ptr[P]` form is currently blocked by the
slicing-check false-positive in `BUGS.md`; the record-direct
`subclass -> Ptr[P]` upcast at the call/init site works). `isinstance(p,
Sub)` on a `Ptr[P]` (or a bare `P` borrow) dispatches at runtime via
`dynamic_cast` on the underlying `P*` for an inheritance conformer, or via
`tpy::dyn_adapter_cast<P,Sub>` for a structural conformer (behind the `P*` a
structural `Sub` is physically an `Adapter<P,Sub>` / `RefAdapter<P,Sub>`, so
the helper tries both shapes and projects `.inner`); codegen picks the cast
by `directly_implements_dynamic`. A check type that neither inherits nor
structurally conforms is rejected (can never match). Owning wrappers
(`Box[P]` / `Rc[P]`) dispatch the same way via deref-view narrowing on the
payload pointer (`docs/LANGUAGE_FEATURES.md`, "Deref-view narrowing"). Static
(non-`@dynamic`) protocols are rejected as `Ptr` element types -- they
have no runtime representation to dispatch through
(`tpyc/sema/type_ops.py:265`).

\* `BaseConcrete` here is a concrete class that inherits a `@dynamic` protocol
(transitively), e.g. `class BaseExc(Throwable)` where `Throwable` is `@dynamic`.
This is a separate surface from `Optional[Pet]` (direct `@dynamic` protocol),
which is now allowed at a free-function parameter position and as an init-only function-local
(both lowering to `const Pet*`; rvalue rebind rejected) but
still rejected in storage / return / field / container / global positions (see Phase 8).
The two columns are complementary:
direct `@dynamic` protocol types are for structural conformance + Adapter dispatch;
concrete class roots with a `@dynamic` parent are for class-hierarchy dispatch
where `isinstance(opt, Subclass)` and `dynamic_cast` work uniformly.

## `Optional[ConcreteRoot]` Class Dispatch (Phase 18 + 19)

When a concrete class transitively inherits a `@dynamic` protocol, the class
participates in a real C++ inheritance hierarchy with a vtable rooted at the
`@dynamic` protocol's abstract base. This unlocks `isinstance` + `dynamic_cast`
dispatch on `Optional[ConcreteRoot]` parameters without changing the
representation:

User-defined hierarchy example (the rule applies to any concrete class
that transitively inherits a `@dynamic` protocol; the built-in
`BaseException` tree activates via the stdlib `Throwable` protocol):

```python
from tpy import dynamic
from typing import Optional, Protocol

@dynamic
class Audible(Protocol):            # user-defined @dynamic protocol
    def sound(self) -> str: ...

class Animal(Audible):              # concrete class root
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def sound(self) -> str:
        return "..."

class Dog(Animal):                  # override propagates virtually (Phase 18)
    def sound(self) -> str:
        return self.name + " says woof"

def greet(a: Optional[Animal]) -> str:
    if a is None:
        return "<none>"
    if isinstance(a, Dog):          # dynamic_cast<const Dog*>(a) != nullptr
        return a.sound()            # virtual dispatch -> Dog::sound()
    return a.sound()                # virtual dispatch -> Animal::sound()

print(greet(Dog("Rex")))            # "Rex says woof"  (rvalue temp materialized as Dog, not Animal)
```

The same shape activates automatically for the built-in `BaseException`
tree: `Throwable` (declared in `tpy._core._types`) is the `@dynamic`
root, `BaseException` is the concrete-class root that inherits it, and
all exception subclasses (`Exception`, `ValueError`, `OSError`, ...)
participate via the existing inheritance chain. `isinstance(exc_val, ValueError)`
inside an `__exit__` body lowers to `dynamic_cast<const ::tpy::ValueError*>(exc_val) != nullptr`.

**Predicate:** `is_polymorphic_class_type(typ, registry)` in `tpyc/typesys.py`.
Returns True iff `typ` is a `NominalType` for a concrete (non-protocol) class
whose MRO contains a record with at least one `@dynamic` implemented protocol.
Cached lazily on `RecordInfo._is_polymorphic_class` (stable after Phase-1 sema).

**Codegen behavior:**

- **Argument passing** (`_gen_optional_ptr_arg`): rvalue temp for an
  `Optional[BaseExc]` slot is materialized at the rvalue's actual class
  (`ValErr __tmp = ValErr("x"); take(&__tmp);`), then C++ implicit pointer
  upcast handles `&__tmp` -> `const BaseExc*`. No slicing.
- **Init-only local** (`_gen_pointer_local_init`): same fix -- the typed slot
  uses the rvalue's class.
- **isinstance lowering** (`_gen_call`): emits
  `(dynamic_cast<const Sub*>(e_ptr) != nullptr)`; tuple form is OR of casts.
  Reads `var_decl` (the declared, not narrowed, type) so the gate is sound
  for the original `Optional[Polymorphic]` declaration.
- **Cast-and-cache narrowing** (`_emit_isinstance_extractions`): for
  `if isinstance(var, Sub):` with a strict subclass `Sub`, emits
  `(const) Sub& __var = *dynamic_cast<(const) Sub*>(var);` once at branch
  entry; subsequent reads of `var` route through `__var`. Const-ness
  matches the source (`deep_const_borrow_params` / `const_ref_params`).
  Skipped when `narrowed_type == source_inner` (identity from `is not None`,
  no cast needed) and for tuple form (no single subclass).
- **Sema** (`_analyze_isinstance` + `_isinstance_facts`): both Optional-direct
  and post-`is not None`-narrowed paths accept subclass check types and
  produce a strict-subclass narrowing fact for the true branch; equality is
  treated as a degenerate subclass via `is_subclass_of_or_equal`. The
  `polymorphic_source_inner` helper (`tpyc/typesys.py`) centralizes the
  "polymorphic-class source?" predicate used at all four sites (sema narrowing,
  fact filter, codegen bool-check, cast-and-cache extraction); it unwraps
  `readonly` so `readonly[Optional[Polymorphic]]` params dispatch correctly
  (regression-tested by `isinstance_readonly_opt_poly`).

**What does not work yet:**

- *Rvalue rebind of a polymorphic subclass into a local.* The shared rebind
  slot can't preserve dynamic type without heap allocation. Sema-time error
  with a "use parameter-passing or a typed local" hint. Addressed by
  Phase 20's slicing-site sema rejection plus the `Box[@dynamic Root]`
  owned storage path.
- *Storage and rvalue return positions* of `Optional[Polymorphic]`. Field
  declarations and `return ValErr(...)` in a function returning
  `Optional[BaseExc]` would dangle (storage-form Optional has the same
  lifetime/slicing issue). Addressed by Phase 20: rejected at sema with a
  "use `Box[Throwable]` for stored ownership" hint, and `Box[@dynamic Root]`
  becomes the supported form. For the exception hierarchy specifically,
  Phase 20 also adds the `raise <expr>` -> `__raise__` desugar so a stored
  `Box[Throwable]` can be re-raised polymorphically.
- *(Activated)* The built-in `BaseException` tree is `@dynamic`-rooted via
  the `Throwable` protocol in `tpy._core._types`. `isinstance(exc_val, X)`
  in `__exit__` bodies works for any subclass of `BaseException`
  (`ValueError`, `OSError`, `RuntimeError`, ...). Tested in
  `tests/cases/control_flow/with_exit_isinstance_class_dispatch`,
  `isinstance_optexc_outside_exit`, `isinstance_optexc_subclass_field_read`
  (subclass-only field read), and `isinstance_readonly_opt_poly` (readonly
  param + tuple form).

**Relation to direct `Optional[Pet]`:** rejected (Phase 8), with the rejection
now known to be partially obsolete (parameter and init-only-local positions
could lower the same way once the rejection is moved out of `validate_type`).
TODO.md tracks the narrowing.

## C++ Code Generation

For each `@dynamic` protocol, the compiler generates four artifacts:

**1. C++20 concept** (for `T: Proto` static dispatch):

The concept uses `__{Name}_Concept__` so the clean name is free for the base class:

```cpp
template<typename T>
concept __Speakable_Concept__ = requires(T& t) {
    { t.speak() } -> std::same_as<void>;
};
```

**2. Abstract base class** (vtable target, gets the protocol's clean name):

```cpp
struct Speakable {
    virtual void speak() = 0;
    virtual ~Speakable() = default;
};
```

**3. Owning adapter** (partial specialization of `tpy::Adapter`, at global scope):

```cpp
template<__Speakable_Concept__ T>
struct tpy::Adapter<Speakable, T> : Speakable {
    T inner;
    template<typename... Args>
    Adapter(Args&&... args) : inner(std::forward<Args>(args)...) {}
    void speak() override { inner.speak(); }
};
```

**4. Ref adapter** (partial specialization of `tpy::RefAdapter`, at global scope):

```cpp
template<__Speakable_Concept__ T>
struct tpy::RefAdapter<Speakable, T> : Speakable {
    T& inner;
    RefAdapter(T& ref) : inner(ref) {}
    void speak() override { inner.speak(); }
};
```

When a class explicitly inherits a `@dynamic` protocol (`class Dog(Speakable)`), the
compiler generates direct C++ inheritance (`struct Dog : Speakable`) with `override`
on matching methods. This eliminates adapter wrapping entirely -- the object IS-A
`Speakable` and can be passed directly. The adapters are only used for structural
conformance (types that satisfy the protocol without explicit inheritance).

**Naming convention**: The base class gets the protocol's clean name (e.g., `Pet`),
making it natural for C++ interop. The concept uses a dunder name (`__Pet_Concept__`)
since it's internal. Adapters live in the `tpy::` namespace as partial specializations
of `tpy::Adapter<Base, T>` and `tpy::RefAdapter<Base, T>`, following the same pattern
as `tpy::EnumUtil<E>`.

### Generic `@dynamic` protocols

When the protocol carries type parameters (e.g. `@dynamic class Awaitable[T]`),
the four artifacts above become class templates parameterized on the protocol's
own type params. tpyc emits the template once; the C++ compiler does
per-instantiation monomorphization.

```cpp
// Concept: protocol-T maps to _T0 (the checked type is T, then the protocol's args)
template<typename T, typename _T0>
concept __Awaitable_Concept__ = requires(T& t) {
    { t.__poll__(std::declval<Waker>()) } -> std::convertible_to<Poll<_T0>>;
};

// Base class: bare names of protocol type params become C++ template params
template<typename T>
struct Awaitable {
    virtual Poll<T> __poll__(Waker waker) = 0;
    virtual ~Awaitable() = default;
};

// Owning + ref adapters: extra template params for the protocol's args; the
// concept abbreviated form `__Awaitable_Concept__<T> __tpy_Impl` desugars to
// `requires __Awaitable_Concept__<__tpy_Impl, T>`. The impl-param uses the
// reserved `__tpy_` prefix so it cannot collide with a user-chosen
// protocol type param.
template<typename T, __Awaitable_Concept__<T> __tpy_Impl>
struct tpy::Adapter<Awaitable<T>, __tpy_Impl> : Awaitable<T> { /* ... */ };
template<typename T, __Awaitable_Concept__<T> __tpy_Impl>
struct tpy::RefAdapter<Awaitable<T>, __tpy_Impl> : Awaitable<T> { /* ... */ };
```

Each used `Awaitable[int32]` resolves to `Awaitable<int32_t>`, and the adapter
partial-spec selects on `tpy::Adapter<Awaitable<int32_t>, ConcreteImpl>`.
`Awaitable[int32]` and `Awaitable[str]` therefore have independent vtables.
Direct C++ inheritance threads the parameterized form through to derived
records (`class IntBox(Awaitable[int32])` -> `struct IntBox : Awaitable<int32_t>`).
A generic @dynamic protocol extending another generic @dynamic protocol
preserves the type param in the base clause (`struct Counter<T> : Source<T>`).

Covariant return wrapping (e.g. `inner.foo()` -> `std::string(inner.foo())`
for protocol returns `str`) fires per the concrete return type, so a method
returning a generic `T` is not auto-wrapped: users should spell `StrView`
consistently if the instantiation is `T = str`.

## Object Safety

Not all protocols can be `@dynamic`. The compiler validates at definition site:

- Method signatures must be concrete after substituting the protocol's own type
  params (a generic protocol may use its own `T`; it may not introduce a fresh
  per-method type param)
- No `Self` type (deferred -- `Self` support may be added later with restrictions)
- No static methods (no receiver to dispatch on)
- Markerless `@dynamic` protocols **are allowed** -- they act as phylum tags for the polymorphism predicate (`is_polymorphic_class_type`) without committing to a virtual method contract. The emitted C++ shape is an empty abstract base struct (`struct X { virtual ~X() = default; };`) and a trivially-satisfied concept (`concept __X_Concept__ = true;`). No method dispatch goes through the vtable in this case; the marker is purely a sema-level tag consumed by `Optional[ConcreteRoot]` class dispatch. (Note: the stdlib `Throwable` was markerless pre-Phase 20 and is no longer -- Phase 20 gives it virtual `clone` and `__raise__` so it can participate in `Box[Throwable]` storage and the `raise <expr>` desugar. The general "markerless allowed" rule still applies to other protocols that want phylum-only behavior.)
- The type-parameter name `__tpy_Impl` is reserved for the adapter codegen and
  cannot be used by user protocols (rejected at codegen time)

A non-object-safe protocol with `@dynamic` is a compile error.

### Generic `@dynamic` + direct inheritance restriction

When a record explicitly inherits a generic `@dynamic` protocol (`class IntSink(Sink[int32])`),
the override must match the base virtual's `::tpy::param_val_or_ref_t<T>` shape. For methods
whose `T` appears only in the return position, this works -- the direct-inheritance codegen
emits the correct override. For methods with `T` in *parameter* position, the override
currently emits the concrete type without the trait wrap, producing a signature mismatch
that leaves the derived class abstract. As an interim guard, sema rejects this combination
with a diagnostic pointing users at the structural-conformance (adapter) path, which
handles parameterized parameters correctly. The codegen fix is tracked in `BUGS.md`.

## Conformance

Dynamic protocols use the same structural conformance as static protocols. A concrete
type conforms if it has all required methods with compatible signatures. No explicit
`extends` is needed:

```python
@dynamic
class Pet(Protocol):
    def make_noise(self) -> None: ...

class Dog:
    def make_noise(self) -> None:    # structurally conforms to Pet
        print("Woof")

dog: Pet = Dog()                     # OK: Dog has make_noise() -> None
```

This is consistent with non-dynamic protocols (e.g., `Sized` checks for `__len__`
without requiring `extends`). Explicit `extends` remains for marker protocols only.

## Known Gaps

Compiler infrastructure issues (not blocked on `Box[P]`):

- **Protocol fields on `@dynamic`** -- field access on protocol-typed variables fails
  in sema ("Cannot access field"). Virtual getters are generated in the base class but
  sema doesn't resolve field access through the erased type.
- **`Box[P].set` / `take` / `clone` all work for abstract P.**
  `set` was rewritten to use `tpy::heap_replace` (in-place reconstruct for concrete
  T, free+take for abstract). `take` was rewritten to use `tpy::transfer_ownership`
  (with the new `own_return_t<T>` return-shape alias). `clone` carries a
  per-method `T: Copyable` shadow bound; abstract `@dynamic` P does not conform
  to Copyable (no usable copy ctor at the C++ level), so `Box[Pet].clone()` is
  rejected at sema with a clean diagnostic
  (`Method 'clone' requires type parameter 'T' to satisfy 'Copyable'`).

## Future Extensions

- **`Self` type in `@dynamic`** -- may be supported with restrictions (e.g., `Self` in
  return position only, behind `Box`).
- **Multiple protocol conformance** -- `pet: Pet & Drawable` for intersection types.
- **`Arc[T]`** -- atomic-refcount sibling of `Rc[T]` for multi-threaded sharing.
  Same shape; refcount ops become atomic CAS loops.
- **Fat-pointer protocol reference (`dyn[P]`) for structural borrow coercion.**
  The current model lowers `@dynamic P` to a C++ abstract base with an *embedded*
  vtable, so a protocol reference is a thin `P*`. That makes a *borrowed* structural
  conformer un-coercible to `Ptr[P]`: a structural conformer (one that has P's methods
  but does not inherit P's base) needs an `Adapter<P, T>` object to carry the vtable,
  and a borrow has nowhere to store one with a long-enough lifetime (a function-local
  adapter would dangle on return). Structural conformance therefore only works where
  *owned* storage holds the adapter -- `Box[P]`, `Rc[P]`.

  A Rust-`&dyn` / Go-interface style **fat pointer** -- `(data*, const PVtable*)` where
  the vtable is a `static` per-`(P, T)` global -- removes that wall: the vtable is
  `'static` (nothing to store) and the data pointer aliases the original object, so a
  bare borrowed structural conformer coerces with no adapter and no dangling. It would
  also unify structural and inheritance conformance through one path (retiring the
  `Adapter` for the protocol case) and decouple conformance from object layout
  (duck-typing without inheritance).

  Not adopted as the default because it conflicts with shipped commitments:
  protocol references would become two words (fighting the low-latency goal for
  pointer/container-heavy code); the custom fat-pointer ABI breaks the clean `@native`
  C++ interop the abstract-base model gives (`@native` code takes `P&`); and `isinstance`
  would need a vtable type-id (reimplementing RTTI) instead of leaning on C++
  `dynamic_cast`. The realistic path, if structural *borrow* coercion ever becomes a
  recurring need, is an **opt-in** `dyn[P]` two-word reference type *alongside* the
  inheritance model -- common code keeps thin `P*` + C++ interop; only code that wants
  structural borrow coercion pays the second word. Two coexisting models is its own
  complexity, so gate this on demonstrated demand. (Owned structural conformance via
  `Box[P]`/`Rc[P]` already works and does not need this.)

## Implementation Steps

1. **`@dynamic` decorator** (done) -- parser recognizes `@dynamic` on protocol classes,
   sema stores `is_dynamic` flag on ProtocolInfo, object-safety validation at definition
   site (markerless protocols are allowed as phylum tags, no Self type; generic `@dynamic`
   is supported -- see step 12)
2. **Abstract base + adapter codegen** (done) -- generate base class (e.g., `struct Pet`),
   `tpy::Adapter<Pet, T>`, and `tpy::RefAdapter<Pet, T>` for each `@dynamic` protocol.
   Base class in user namespace; adapters as partial specializations at global scope.
3. **Protocol-typed locals** (done) -- `pet: Pet = Dog()` generates stack slot +
   pointer-local (`Pet*`). Direct inheritors use plain concrete slot;
   structural conformance uses owning adapter slot. Reassignment allocates new slots.
4. **Protocol-typed function params** (done) -- `def f(pet: Pet)` generates
   `f(Pet& pet)`. Call-site dispatch: direct inheritors pass by implicit upcast
   (zero cost); structural lvalues use RefAdapter (zero-copy, mutations visible);
   structural rvalues use owning Adapter.
5. **Direct C++ inheritance** (done) -- when a class explicitly inherits a `@dynamic`
   protocol (`class Dog(Pet)`), the C++ struct inherits the base class and matching
   methods get `override`. Eliminates adapter wrapping entirely.
6. **Return types** -- allow returning protocol-typed values when provably long-lived
   (globals, parameters); error on returning local adapters
7. **`Box[P]` integration** (done) -- heap-allocated dynamic values for fields,
   containers, and (mostly) unrestricted returns. `Box[P]` accepts inheritance
   and structural conformers; codegen wraps as `std::unique_ptr<P>(new Adapter<P, U>(...))`
   for structural or `std::unique_ptr<P>(new ConcreteInheritor(...))` for inheritance.
   See `tpy::own_param_t<T>`, `tpy::is_dyn_protocol_base<T>`, `tpy::heap_take` /
   `tpy::heap_release`. Cross-references: `docs/LANGUAGE_FEATURES.md` Box[P] section;
   `docs/SEND_SYNC_DESIGN.md` OQ5 for storage-form `Box[Send[P]]` composition.
