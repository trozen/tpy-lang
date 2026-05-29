# Optional[@dynamic protocol] is allowed as an init-only function-local: it
# lowers to a `const Pet*` pointing at the materialized rvalue (same shape as
# the parameter position). `is None`, isinstance subclass dispatch, and method
# calls all work; the wrapper is a plain pointer so no value-repr of the
# abstract base is needed. The rvalue-rebind case stays rejected (see
# error_opt_dyn_protocol_local_rebind).
from typing import Protocol, Optional
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"

    def bark(self) -> str:
        return "woof"


class Cat:                       # structural conformer, no inheritance
    def name(self) -> str:
        return "cat"


def describe() -> str:
    p: Optional[Pet] = Dog()     # tpyc: ok
    if p is None:
        return "none"
    if isinstance(p, Dog):
        return "dog:" + p.bark()
    return p.name()


def describe_structural() -> str:
    # Structural conformer init materializes an Adapter<Pet, Cat> slot so &slot
    # upcasts to const Pet*.
    p: Optional[Pet] = Cat()     # tpyc: ok
    if p is None:
        return "none"
    return p.name()


def main() -> None:
    print(describe())
    print(describe_structural())


main()
