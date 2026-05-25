# isinstance(self, Sub) in a method body on a polymorphic class. Sema routes
# through polymorphic-dispatch (self's declared type is the enclosing record,
# which inherits a @dynamic protocol); codegen's `lookup_var_type('self')`
# now surfaces the record type via `current_method_record_type`, and the
# `polymorphic_cast_arg` helper returns `this` for self (already pointer-
# shaped). dynamic_cast<Sub*>(this) lets the body call subclass-only methods.
from typing import Protocol
from tpy import dynamic, readonly


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

    @readonly
    def describe(self) -> str:
        # const method: self lowers to `const Pet* this`; dynamic_cast<const Dog*>(this)
        # picks the subclass; narrowed local binds `const Dog& __self` (via cast-and-cache)
        # though here we use the if-init form so the read routes through __self_ptr.
        if isinstance(self, Dog):  # tpyc: ok
            return "dog: " + self.bark()
        if isinstance(self, Cat):  # tpyc: ok
            return "cat: " + self.purr()
        return "pet: " + self._name

    @readonly
    def kind(self) -> str:
        # Tuple form on self: multi-cast OR, no narrowing (matches the bare
        # polymorphic param test's `kind()` shape).
        if isinstance(self, (Dog, Cat)):  # tpyc: ok -- tuple form (no narrowing)
            return "mammal: " + self._name
        return "other: " + self._name

    @readonly
    def assert_dog(self) -> str:
        # assert isinstance(...) takes the fresh-cast reference-local path
        # (no if-init pre-bind). For self, the cast input is `this`.
        assert isinstance(self, Dog)  # tpyc: ok
        return "ASSERT: " + self.bark()

    def mutate_then_describe(self, suffix: str) -> str:
        # non-const method: self lowers to `Pet* this`; cast emits non-const `Dog*`.
        # `self._name = ...` mutates the *current* dynamic type's slot (works for
        # any concrete subclass).
        self._name = self._name + suffix
        if isinstance(self, Dog):  # tpyc: ok
            return "MUT-DOG: " + self.bark()
        return "MUT-PET: " + self._name


class Dog(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)

    @readonly
    def bark(self) -> str:
        return "woof from " + self._name


class Cat(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)

    @readonly
    def purr(self) -> str:
        return "purr from " + self._name


def main() -> None:
    d = Dog("rex")
    c = Cat("whiskers")
    p = Pet("plain")
    print(d.describe())
    print(c.describe())
    print(p.describe())
    print(d.kind())
    print(c.kind())
    print(p.kind())
    print(d.assert_dog())
    print(d.mutate_then_describe("!"))
    print(p.mutate_then_describe("?"))


main()
