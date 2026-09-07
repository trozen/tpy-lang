# A NAME arm beside a CALL arm at an `Own[@dynamic protocol]` return: only the
# call-pair ternary is admitted. Concretely, `make() if flag else q` mixes a
# call arm with a plain name arm; TPy rejects that ternary today.
from typing import Protocol
from tpy import Own, dynamic, readonly


@dynamic
class Pet(Protocol):
    @readonly
    def name(self) -> str: ...


class Dog(Pet):
    @readonly
    def name(self) -> str:
        return "Rex"


def make() -> Own[Pet]:
    return Dog()


def pick(flag: bool, q: Own[Pet]) -> Own[Pet]:
    return make() if flag else q  # tpyc: error(/expr.ifexpr/)


def main() -> None:
    print(pick(True, Dog()).name())


main()
