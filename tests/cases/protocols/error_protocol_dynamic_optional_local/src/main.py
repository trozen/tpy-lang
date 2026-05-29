# Optional[@dynamic protocol] is rejected as a local variable annotation:
# the parameter position is the only one with a pointer-repr lowering (a
# local would need value-repr storage of an abstract base, or a rebind slot
# that can't retype per-rvalue).
from typing import Protocol, Optional
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "Rex"


def main() -> None:
    p: Optional[Pet] = Dog()  # tpyc: error(/Optional\[Pet\] is only supported at a parameter position/)
    print(p is None)


main()
