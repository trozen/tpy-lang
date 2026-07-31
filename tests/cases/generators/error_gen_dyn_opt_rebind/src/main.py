# The @dynamic-protocol rvalue-rebind reject fires in a resumable frame
# body too (sync sibling: protocols/error_opt_dyn_protocol_local_rebind);
# the per-site frame slots do not lift it for erased-protocol locals.
from typing import Iterator, Optional, Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


class Cat(Pet):
    def name(self) -> str:
        return "cat"


def gen() -> Iterator[str]:
    p: Optional[Pet] = Dog()  # tpyc: error(/rvalue rebind is not yet supported/)
    yield "start"
    p = Cat()
    if p is not None:
        yield p.name()


def main() -> None:
    for s in gen():
        print(s)


main()
