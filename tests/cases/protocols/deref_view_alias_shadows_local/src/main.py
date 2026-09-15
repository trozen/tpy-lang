# A deref-view isinstance narrow reads the payload through a synthesized cast
# pointer spelled `__<var>_ptr`. A user binding of that spelling must not be
# shadowed by it -- the branch reads both. Its own case because the deref-view
# narrow has no CPython equivalent (see no_cpython.txt), unlike the sibling
# shapes in tests/cases/name_shadowing/narrow_alias_shadows_local.
from typing import Protocol

from tpy import dynamic, int32
from tplib.box import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Bird(Pet):
    def name(self) -> str:
        return "bird"

    def chirp(self) -> str:
        return "tweet"


class Fish(Pet):
    def name(self) -> str:
        return "fish"


def describe(b: Box[Pet]) -> None:
    __b_ptr = 21
    if isinstance(b, Bird):  # tpyc: ok
        print("deref_view", b.chirp(), __b_ptr)
    else:
        print("other", b.name(), __b_ptr)


def main() -> None:
    describe(Box(Bird()))
    describe(Box(Fish()))


main()
