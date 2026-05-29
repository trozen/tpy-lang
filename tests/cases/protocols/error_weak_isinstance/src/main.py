# A non-owning Weak[Pet] handle has no deref view -- the payload is reachable
# only after upgrade(). isinstance(w, Dog) is rejected (rather than silently
# folding to False) with a hint to upgrade() first.
from typing import Protocol
from tpy import dynamic
from tplib.rc import Weak


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


def via_weak(w: Weak[Pet]) -> str:
    if isinstance(w, Dog):       # tpyc: error(/non-owning handle with no deref view/)
        return "dog"
    return "other"
