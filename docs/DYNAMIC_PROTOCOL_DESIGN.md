# Dynamic Protocol Dispatch -- Design

Extracted from `PROTOCOL_DESIGN.md` section 12.

## Progress

| Step | Description | Status |
|------|-------------|--------|
| 1 | `@dynamic` decorator, parser + sema + object-safety validation | Done |
| 2 | Abstract base + adapter codegen (`__tpy_Base_`, `__tpy_Adapter_`, `__tpy_RefAdapter_`) | Done |
| 3 | Protocol-typed locals (stack slot + pointer-local) | Done |
| 4 | Protocol-typed function params (`Base&`, call-site dispatch) | Done |
| 5 | Direct C++ inheritance (`class Dog(Pet)` -> `struct Dog : __tpy_Base_Pet`) | Done |
| 5a | Conditional/loop reassignment (hoisted `std::optional` slots) | Done |
| 6 | Return types (provably long-lived values only) | Done |
| 7 | `@dynamic` protocol params in record methods | Deferred |
| 8 | `Optional[Pet]` sema rejection | Done |
| 9 | Protocol field access through erased type | Gap |
| 10 | Cross-module `@dynamic` protocols | Gap |
| 11 | `@dynamic` extending `@dynamic` (base class inheritance chain) | Gap |
| 12 | Generic `@dynamic` protocols | Future |
| 13 | `Box[P]` integration (heap-allocated dynamic values) | Future |
| 14 | Record fields typed as `@dynamic` protocol (needs `Box[P]`) | Future |
| 15 | `list[Box[P]]` heterogeneous containers | Future |

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
| `pet: Speakable` (param) | dynamic | `void f(__tpy_Base_Speakable& pet)` |
| `pet: Speakable` (local) | dynamic | `__tpy_Base_Speakable* pet` (pointer-local) |
| `T: Speakable` (type bound) | static | `template<Speakable T> void f(T& pet)` |

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
inheritance from `__tpy_Base_Pet`). Otherwise an owning adapter wraps the value:

```python
pet: Pet = Dog()       # Dog(Pet) -> direct inheritance
pet.make_noise()
```

Generated C++ (direct inheritance):

```cpp
Dog __slot_1{Dog()};
__tpy_Base_Pet* pet = &__slot_1;
pet->make_noise();   // virtual dispatch
```

Generated C++ (structural conformance, no explicit inheritance):

```cpp
__tpy_Adapter_Pet<Parrot> __slot_1{Parrot()};
__tpy_Base_Pet* pet = &__slot_1;
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
__tpy_Base_Pet* pet = &__slot_1;
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
__tpy_Base_Pet* pet = &__slot_1;
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
void greet(__tpy_Base_Pet& pet) {
    pet.make_noise();
}

// Direct inheritor (lvalue): implicit upcast to Base&
greet(my_dog);

// Structural conformance (lvalue): ref adapter, zero-copy, mutations visible
__tpy_RefAdapter_Pet<Parrot> __tmp_1{parrot};
greet(__tmp_1);

// Structural conformance (rvalue): owning adapter
__tpy_Adapter_Pet<Parrot> __tmp_2{Parrot()};
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

**1. C++20 concept** (for `T: Proto` static dispatch -- same as non-dynamic):

```cpp
template<typename T>
concept Speakable = requires(T& t) {
    { t.speak() } -> std::same_as<void>;
};
```

**2. Abstract base class** (vtable target):

```cpp
struct __tpy_Base_Speakable {
    virtual void speak() = 0;
    virtual ~__tpy_Base_Speakable() = default;
};
```

**3. Owning adapter template** (for locals and rvalue call-site args):

```cpp
template<Speakable T>
struct __tpy_Adapter_Speakable : __tpy_Base_Speakable {
    T inner;
    template<typename... Args>
    __tpy_Adapter_Speakable(Args&&... args) : inner(std::forward<Args>(args)...) {}
    void speak() override { inner.speak(); }
};
```

**4. Ref adapter template** (for lvalue call-site args, zero-copy):

```cpp
template<Speakable T>
struct __tpy_RefAdapter_Speakable : __tpy_Base_Speakable {
    T& inner;
    __tpy_RefAdapter_Speakable(T& ref) : inner(ref) {}
    void speak() override { inner.speak(); }
};
```

When a class explicitly inherits a `@dynamic` protocol (`class Dog(Speakable)`), the
compiler generates direct C++ inheritance (`struct Dog : __tpy_Base_Speakable`) with
`override` on matching methods. This eliminates adapter wrapping entirely -- the object
IS-A `__tpy_Base_Speakable` and can be passed directly. The adapters are only used for
structural conformance (types that satisfy the protocol without explicit inheritance).

**Naming convention**: `__tpy_Base_{Name}`, `__tpy_Adapter_{Name}`, `__tpy_RefAdapter_{Name}`.
The `__tpy_` prefix marks these as compiler-internal. The role prefix (`Base_`, `Adapter_`,
`RefAdapter_`) groups related types together.

## Object Safety

Not all protocols can be `@dynamic`. The compiler validates at definition site:

- All methods must have concrete (non-generic) signatures
- No `Self` type (deferred -- `Self` support may be added later with restrictions)
- No static methods (no receiver to dispatch on)
- Marker protocols cannot be `@dynamic` (no methods to dispatch)

A non-object-safe protocol with `@dynamic` is a compile error.

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

- **`Optional[Pet]`** -- falls through to broken C++ (`auto* = nullptr`). Needs sema
  error rejecting `Optional` of a `@dynamic` protocol (until `Box[P]` exists).
- **Protocol fields on `@dynamic`** -- field access on protocol-typed variables fails
  in sema ("Cannot access field"). Virtual getters are generated in the base class but
  sema doesn't resolve field access through the erased type.
- **Cross-module `@dynamic`** -- importing a `@dynamic` protocol from another module
  and using it as a variable type fails ("Type mismatch: expected Pet, got Dog").
  The `is_dynamic` flag or protocol conformance isn't resolved across module boundaries.
- **`@dynamic` extending `@dynamic`** -- `__tpy_Base_Child` doesn't inherit from
  `__tpy_Base_Parent`, so passing a `Child`-typed variable to a `Parent`-typed param
  fails at C++ level. Needs `__tpy_Base_Child : __tpy_Base_Parent` inheritance chain.
- **Method params with `@dynamic` protocol type** -- sema rejects protocol types as
  method parameters ("Protocols are only valid for free function parameters"). Keep
  rejection for now; revisit when use cases arise.

## Future Extensions

- **`Box[P]`** -- heap-owned dynamic value for fields, containers, returns. Requires
  `Box[T]` implementation (Phase 6 of move semantics).
- **`Rc[P]`** -- shared-ownership dynamic value for reference-counted sharing.
- **`Self` type in `@dynamic`** -- may be supported with restrictions (e.g., `Self` in
  return position only, behind `Box`).
- **Generic `@dynamic` protocols** -- e.g., `@dynamic class Comparable(Protocol): def __lt__(self, other: Self) -> bool: ...`
  Requires Self support first.
- **Multiple protocol conformance** -- `pet: Pet & Drawable` for intersection types.
- **`list[Box[P]]`** -- heterogeneous containers with heap-owned dynamic values.
  Requires `Box[P]` first.

## Implementation Steps

1. **`@dynamic` decorator** (done) -- parser recognizes `@dynamic` on protocol classes,
   sema stores `is_dynamic` flag on ProtocolInfo, object-safety validation at definition
   site (no marker protocols, no generic protocols, no Self type)
2. **Abstract base + adapter codegen** (done) -- generate `__tpy_Base_Proto`,
   `__tpy_Adapter_Proto<T>`, and `__tpy_RefAdapter_Proto<T>` for each `@dynamic`
   protocol in the header
3. **Protocol-typed locals** (done) -- `pet: Pet = Dog()` generates stack slot +
   pointer-local (`__tpy_Base_Pet*`). Direct inheritors use plain concrete slot;
   structural conformance uses owning adapter slot. Reassignment allocates new slots.
4. **Protocol-typed function params** (done) -- `def f(pet: Pet)` generates
   `f(__tpy_Base_Pet& pet)`. Call-site dispatch: direct inheritors pass by implicit
   upcast (zero cost); structural lvalues use RefAdapter (zero-copy, mutations visible);
   structural rvalues use owning Adapter.
5. **Direct C++ inheritance** (done) -- when a class explicitly inherits a `@dynamic`
   protocol (`class Dog(Pet)`), the C++ struct inherits `__tpy_Base_Pet` and matching
   methods get `override`. Eliminates adapter wrapping entirely.
6. **Return types** -- allow returning protocol-typed values when provably long-lived
   (globals, parameters); error on returning local adapters
7. **`Box[P]` integration** (future) -- heap-allocated dynamic values for fields,
   containers, and unrestricted returns
