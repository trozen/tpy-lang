# An init-only Optional[@dynamic protocol] local is allowed, but rebinding it
# to a fresh rvalue of a different subclass is rejected: the shared rebind
# slot can't be retyped per rvalue without slicing the dynamic type. (Init-only
# is covered by opt_dyn_protocol_local.)
from typing import Protocol, Optional
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


class Cat(Pet):
    def name(self) -> str:
        return "cat"


def f() -> str:
    # Annotation sits on the init line: codegen detects the conflict there
    # (the shared slot can't be retyped) once it sees the rebind below.
    p: Optional[Pet] = Dog()     # tpyc: error(/rvalue rebind is not yet supported/)
    p = Cat()
    return p.name()
