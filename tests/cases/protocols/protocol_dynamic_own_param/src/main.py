# Own[P] for abstract @dynamic protocol P: function param and return type
# both lower to std::unique_ptr<P>; body-side `p.method()` emits `p->method()`.
from typing import Protocol
from tpy import dynamic, Own, StrView

@dynamic
class Pet(Protocol):
    def name(self) -> StrView:
        ...

@dynamic
class NamedPet(Pet, Protocol):
    def species(self) -> StrView:
        ...

class Parrot:
    _name: str
    def __init__(self, n: str) -> None:
        self._name = n
    def name(self) -> StrView:
        return self._name

class Dog:
    _name: str
    def __init__(self, n: str) -> None:
        self._name = n
    def name(self) -> StrView:
        return self._name

class Tabby:
    _name: str
    def __init__(self, n: str) -> None:
        self._name = n
    def name(self) -> StrView:
        return self._name
    def species(self) -> StrView:
        return "cat"

def make_parrot() -> Own[Pet]:
    return Parrot("Polly")

def make_dog() -> Own[Pet]:
    return Dog("Rex")

def make_tabby() -> Own[NamedPet]:
    return Tabby("Whiskers")

def widen_to_pet(np: Own[NamedPet]) -> Own[Pet]:
    # Forward an Own[ChildProtocol] into an Own[ParentProtocol] return slot.
    # _dyn_protocol_forward_ok takes the _protocol_inherits_from branch here
    # (qualified_name differs but NamedPet inherits Pet); both ends stay
    # unique_ptr-shaped and the upcast happens via the converting move ctor.
    return np

def speak_and_forward(p: Own[Pet]) -> Own[Pet]:
    # Body-side method access on an Own[P] param: emits p->name(), not p.name().
    print(p.name())
    return p

def pick(use_parrot: bool) -> Own[Pet]:
    # Ternary of Own[P]-returning calls: sema strips OwnType from the ternary
    # itself, so _resolve_own_source_type returns None. The forward path must
    # still kick in via the "arg_type is_dyn_protocol" fallback, otherwise
    # codegen would emit ill-formed `std::make_unique<Adapter<Pet, Pet>>(...)`.
    return make_parrot() if use_parrot else make_dog()

def main() -> None:
    speak_and_forward(make_parrot())
    speak_and_forward(make_dog())
    # Chained call on an Own[P]-returning function -- pins the
    # resolved_function_info.return_type branch of _receiver_is_own_dyn.
    print(make_parrot().name())
    print(pick(True).name())
    print(pick(False).name())
    print(widen_to_pet(make_tabby()).name())

main()
