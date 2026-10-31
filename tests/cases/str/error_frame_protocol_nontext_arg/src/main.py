# A value argument to a generator parameter of a non-text protocol is refused
# too (BUGS.md#frame-protocol-value-arg); the concrete `int32` twin compiles.
from typing import Iterator
from tpy import Hashable


def gen_hash(v: Hashable) -> Iterator[int]:
    yield hash(v)


def main() -> None:
    n = 3
    # the subject: an int32 at a generator Hashable parameter
    it = gen_hash(n)  # tpyc: error(/a 'int32' argument to the generator parameter 'v: Hashable' is not supported yet; declare the parameter with the argument's type/)
    print([h for h in it])


main()
