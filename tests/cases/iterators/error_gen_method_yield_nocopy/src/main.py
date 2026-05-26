# Error: a generator METHOD yielding a non-copyable (@nocopy) type is rejected,
# same as free-function generators -- each yielded value is stored by value in
# the iterator slot and __next__() hands out a copy, hitting a deleted copy
# ctor. Enforced at the method-signature registration site. (Handle is defined
# before Box so the registration-time check sees its fully-registered nocopy
# status; forward-referenced records are a known gap, see BUGS.md.)
from typing import Iterator
from tpy import Int32, nocopy


@nocopy
class Handle:
    fd: Int32
    def __init__(self, fd: Int32) -> None:
        self.fd = fd


class Box:
    items: list[Handle]
    def __init__(self) -> None:
        self.items = [Handle(1)]

    def each(self) -> Iterator[Handle]:  # tpyc: error(/Generator cannot yield @nocopy type 'Handle'/)
        for h in self.items:
            yield h


def main() -> None:
    pass


main()
