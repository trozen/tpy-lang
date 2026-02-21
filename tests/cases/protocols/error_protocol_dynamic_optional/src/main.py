# Optional[@dynamic protocol] is rejected (no pointer-repr for erased type)
from tpy import dynamic
from typing import Protocol, Optional

@dynamic
class Pet(Protocol):
    def name(self) -> str: ...

class Dog(Pet):
    def name(self) -> str:
        return "Rex"

def greet(pet: Optional[Pet]) -> None:  # tpyc: error(/Optional\[Pet\] is not supported/)
    pass
