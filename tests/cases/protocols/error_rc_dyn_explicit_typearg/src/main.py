# Rc[Pet] (Pet @dynamic) -- explicit type-arg form fails at sema.
#
# `Rc[Pet].new(...)` uses the explicit-type-arg syntax on a function/method
# call. Sema's general "protocol-as-type-arg-on-call" check (calls.py:2807)
# rejects @dynamic protocols there. The check is independent of Rc's
# specifics -- any user/library function call with `F[Pet](...)` hits it.
# The annotation form `r: Rc[Pet]` is accepted (different path); only the
# call-form explicit-type-arg trips this gate.
from typing import Protocol
from tpy import dynamic
from tplib import Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


def main() -> None:
    r = Rc[Pet].new(Parrot("Polly"))  # tpyc: error(/Protocol type 'Pet' cannot be used as a type argument/)
    print(r.get().name())


main()
