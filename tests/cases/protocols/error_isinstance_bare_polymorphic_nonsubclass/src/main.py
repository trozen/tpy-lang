# isinstance check type must be a subclass of a bare polymorphic-class source.
# Exercises the non-Optional polymorphic dispatch path in _analyze_isinstance --
# `p: Pet` (bare polymorphic class inheriting a @dynamic protocol) is routed
# through _validate_polymorphic_subclass_dispatch, and unrelated check types
# are rejected with the same error as the Optional[Pet] path.
from tpy import dynamic
from typing import Protocol


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    def __init__(self) -> None:
        pass


class OtherDyn(Tagged):
    def __init__(self) -> None:
        pass


def check(p: Pet) -> bool:
    return isinstance(p, OtherDyn)  # tpyc: error(/not subclasses of 'Pet'/)


def main() -> None:
    pass


main()
