# Abstract @dynamic protocol P (here `Pet`) does not conform to Copyable:
# no usable copy ctor at the C++ level and concrete size depends on the
# dynamic type. The Copyable shadow bound on Box.clone rejects this at the
# call site -- the original headline scenario from BUGS.md, now closed by
# the marker protocol. Sibling of error_box_clone_nocopy (which exercises
# the @nocopy / nested-type-arg path); this one exercises the dyn-protocol
# branch of is_type_non_copyable directly.
from typing import Protocol
from tpy import dynamic
from tplib import Box


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
    box: Box[Pet] = Box(Parrot(label="Polly"))
    other = box.clone()  # tpyc: error(/Method 'clone' requires type parameter 'T' to satisfy 'Copyable'.*Pet.*does not conform/)
    print(other.get().name())


main()
