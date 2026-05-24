# Bare polymorphic-class isinstance downcast: source is a class inheriting
# a @dynamic protocol (Tagged -> Pet), C++ representation is a reference
# (Pet& or const Pet&) rather than a pointer like Optional[Pet]. isinstance
# lowers to dynamic_cast<Sub*>(&p) (address-of step) and narrowing binds
# the variable to a Sub& in the true branch via the cast-and-cache path.
from typing import Protocol
from tpy import dynamic, readonly, Int32


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    _name: str

    def __init__(self, n: str) -> None:
        self._name = n

    @readonly
    def name(self) -> str:
        return self._name


class Dog(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)

    @readonly
    def bark(self) -> str:
        return "woof from " + self._name


class Cat(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)


class Bird(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)


def speak(p: Pet) -> str:
    # Single-fact isinstance: pre-bound via C++17 if-init clause; the bool
    # check reduces to `__p_ptr != nullptr` and the narrowed read routes
    # through `(*__p_ptr).bark()` (no separate ref local).
    if isinstance(p, Dog):  # tpyc: ok
        narrowed = p  # tpyc: type(Dog)
        return "DOG: " + narrowed.bark()
    return "pet: " + p.name()


def kind(p: Pet) -> str:
    # Tuple form: multiple dynamic_casts -- if-init can't pre-bind a single
    # local, so the bool check is the multi-cast OR form and the body
    # doesn't narrow (no single Sub to downcast to).
    if isinstance(p, (Dog, Cat)):  # tpyc: ok -- tuple form (no narrowing)
        return "mammal: " + p.name()
    return "other: " + p.name()


def classify_const(p: readonly[Pet]) -> str:
    # Readonly param -- const-borrow source; cast emits `const Dog*`.
    if isinstance(p, Dog):  # tpyc: ok
        narrowed = p  # tpyc: type(Dog)
        return "CONST-DOG: " + narrowed.bark()
    return "const-pet: " + p.name()


def assert_dog(p: Pet) -> str:
    # assert isinstance(...) takes the _emit_isinstance_extractions path with
    # indent_extra=0 (no if-init pre-bind); the fresh-cast reference-local
    # branch emits `Dog& __p = *dynamic_cast<Dog*>(&p)` and narrowing
    # persists for the rest of the scope.
    assert isinstance(p, Dog)  # tpyc: ok
    return "ASSERT: " + p.bark()


def short_circuit(p: Pet, threshold: Int32) -> bool:
    # Inline isinstance fact on `&&` RHS: the narrowed read `p.bark()` on the
    # right of the short-circuit emits `(*static_cast<const Dog*>(&p)).bark()`,
    # not `std::get<Dog>(p)` (the source isn't a variant). static_cast is
    # safe because the `&&` LHS already validated via dynamic_cast.
    return isinstance(p, Dog) and len(p.bark()) > threshold


def main() -> None:
    d = Dog("rex")
    c = Cat("whiskers")
    b = Bird("tweety")
    print(speak(d))
    print(speak(c))
    print(kind(d))
    print(kind(c))
    print(kind(b))
    print(classify_const(d))
    print(classify_const(c))
    print(assert_dog(d))
    print(short_circuit(d, 3))
    print(short_circuit(d, 100))
    print(short_circuit(c, 3))


main()
