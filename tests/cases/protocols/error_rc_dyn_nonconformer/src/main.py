# `Rc[Pet] = Rc.new(NotAPet(...))` -- argument doesn't conform to Pet.
# Pins the diagnostic for the negative path of Rc[@dynamic P].
from typing import Protocol
from tpy import dynamic
from tplib import Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class NotAPet:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    # Missing `name(self) -> str`.


def main() -> None:
    # Regex accepts either the imprecise inference-failure message or the
    # precise protocol-conformance one -- the bounded-factory inference path
    # currently surfaces the former; mirrors error_bound_factory_inference.
    r: Rc[Pet] = Rc.new(NotAPet("oops"))  # tpyc: error(/Cannot infer type arguments for 'new'|does not conform to protocol Pet/)
    print(r.get().name())


main()
