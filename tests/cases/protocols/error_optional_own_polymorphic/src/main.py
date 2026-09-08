# Optional[Own[Polymorphic]] is rejected: a polymorphic Own lowers to
# unique_ptr<P>, so the optional slot would be a double indirection the
# member-access / isinstance lowerings do not thread today. The idiomatic
# nullable owned-polymorphic form is Optional[Box[P]].
from typing import Protocol, Optional
from tpy import dynamic, Own


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


def maybe_adopt(p: Optional[Own[Pet]]) -> str:  # tpyc: error(/not yet supported: a polymorphic .Own\[Pet\]. is already held/)
    if p is None:
        return "none"
    return p.name()
