# A subclass of Weak[Pet] inherits its base's missing deref view: isinstance(w, Dog)
# is rejected exactly as on Weak[Pet] itself (a base is a base).
from typing import Protocol
from tpy import dynamic
from tplib.rc import Weak


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


class WeakSub(Weak[Pet]):
    pass


def via_weak(w: WeakSub) -> str:
    if isinstance(w, Dog):       # tpyc: error(/non-owning handle with no deref view/)
        return "dog"
    return "other"
