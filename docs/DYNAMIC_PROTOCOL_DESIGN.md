# Dynamic Protocol Dispatch -- Design

Extracted from `PROTOCOL_DESIGN.md` section 12.

## Progress

| Step | Description | Status |
|------|-------------|--------|
| 1 | `@dynamic` decorator, parser + sema + object-safety validation | Done |
| 2 | Abstract base + adapter codegen (base class + `tpy::Adapter`, `tpy::RefAdapter`) | Done |
| 3 | Protocol-typed locals (stack slot + pointer-local) | Done |
| 4 | Protocol-typed function params (`Base&`, call-site dispatch) | Done |
| 5 | Direct C++ inheritance (`class Dog(Pet)` -> `struct Dog : Pet`) | Done |
| 5a | Conditional/loop reassignment (hoisted `std::optional` slots) | Done |
| 6 | Return types (provably long-lived values only) | Done |
| 7 | `@dynamic` protocol params in record methods/constructors | Done |
| 8 | `Optional[Pet]` sema rejection | Done |
| 9 | Protocol field access through erased type | Future |
| 10 | Cross-module `@dynamic` protocols | Done |
| 11 | `@dynamic` extending `@dynamic` (base class inheritance chain) | Done |
| 12 | Generic `@dynamic` protocols | Done |
| 13 | `Box[P]` integration (heap-allocated dynamic values) | Done |
| 14 | Record fields typed as `@dynamic` protocol via `Box[P]` | Done |
| 15 | `list[Box[P]]` heterogeneous containers | Done |
| 16 | `Own[P]` as a plain function parameter (with method access on the owned value) | Future (Box's constructor works; general member access via `.` on `Own[P]` is missing) |
| 17 | `Rc[P]` for `@dynamic` P -- shared-ownership erased dyn protocol | Future. Design direction: Rust's `Arc<dyn Trait>` single co-located allocation (refcount header + concrete in one block, drop-fn-in-header for type-erased destruction). Interim workaround: `Rc[Box[P]]` (two allocations: cell + box's pet). See TODO.md. |

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
    def draw(self, x: Int32, y: Int32) -> None: ...
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
with unknown lifetime. These require explicit `Box[P]` (or future `Rc[P]`):

| Context | Direct `Pet` | `Box[Pet]` |
|---------|-------------|------------|
| Local variable | OK (stack adapter) | OK (heap) |
| Function param | OK (reference) | OK |
| Return value | Only if source outlives caller | OK |
| Record field | No (size unknown) | OK |
| `list[Pet]` | No (elements need ownership) | `list[Box[Pet]]` |

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

Each used `Awaitable[Int32]` resolves to `Awaitable<int32_t>`, and the adapter
partial-spec selects on `tpy::Adapter<Awaitable<int32_t>, ConcreteImpl>`.
`Awaitable[Int32]` and `Awaitable[str]` therefore have independent vtables.
Direct C++ inheritance threads the parameterized form through to derived
records (`class IntBox(Awaitable[Int32])` -> `struct IntBox : Awaitable<int32_t>`).
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
- Marker protocols cannot be `@dynamic` (no methods to dispatch)
- The type-parameter name `__tpy_Impl` is reserved for the adapter codegen and
  cannot be used by user protocols (rejected at codegen time)

A non-object-safe protocol with `@dynamic` is a compile error.

### Generic `@dynamic` + direct inheritance restriction

When a record explicitly inherits a generic `@dynamic` protocol (`class IntSink(Sink[Int32])`),
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
- **`Box[P].set` / `clone` / `take` broken for abstract P** -- the method bodies
  placement-new or return abstract P by value (ill-formed). Box's `__init__` /
  `__del__` work; only the post-construction mutation/sharing/extraction methods
  fail. See BUGS.md for the full picture.

## Future Extensions

- **`Self` type in `@dynamic`** -- may be supported with restrictions (e.g., `Self` in
  return position only, behind `Box`).
- **Multiple protocol conformance** -- `pet: Pet & Drawable` for intersection types.
- **`Rc[P]`** -- shared-ownership dynamic value for reference-counted sharing.
  Design direction locked to Rust's `Arc<dyn Trait>` single co-located allocation
  (refcount header + concrete in one block, drop-fn pointer in header). Interim
  workaround: `Rc[Box[P]]`. See TODO.md.

## Implementation Steps

1. **`@dynamic` decorator** (done) -- parser recognizes `@dynamic` on protocol classes,
   sema stores `is_dynamic` flag on ProtocolInfo, object-safety validation at definition
   site (no marker protocols, no Self type; generic `@dynamic` is supported -- see step 12)
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
