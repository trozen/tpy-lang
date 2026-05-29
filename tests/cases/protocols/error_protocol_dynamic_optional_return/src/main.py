# Optional[@dynamic protocol] is rejected as a return type: only the
# parameter position has a pointer-repr lowering, and a returned borrow
# would dangle.
from typing import Protocol, Optional
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "Rex"


def make() -> Optional[Pet]:  # tpyc: error(/Optional\[Pet\] is only supported at a parameter position/)
    return Dog()
