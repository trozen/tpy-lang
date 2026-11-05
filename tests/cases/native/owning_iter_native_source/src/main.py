# iter() of a fresh @native source runs its __iter__ at the call, as CPython
# does for every type (a TPy class: calls/adopts_fresh_arg).
__tpy__ = {"includes": ["native_types.hpp"]}
from typing import Iterator
from tpy import int32, Own
from tpy.extern import native


@native("probe::Source")
class Source:
    def __init__(self) -> None: ...

    def __iter__(self) -> Iterator[int32]: ...


def make() -> Own[Source]:
    return Source()


def main() -> None:
    # the native __iter__ prints before "created"
    it = iter(make())
    print("created")
    got = list(it)
    print("eager", got)


main()
