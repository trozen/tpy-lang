# A value argument to a generator parameter of a protocol inherited from a
# text protocol is refused, as at Stringable itself
# (BUGS.md#frame-protocol-value-arg); the concrete `tuple[int32, str]` twin compiles.
from typing import Iterator, Protocol
from tpy import Stringable


class Text(Stringable, Protocol):
    pass


def gen_text(v: Text) -> Iterator[str]:
    yield str(v)


def main() -> None:
    t = (1, "a")
    # the subject: a tuple at the subprotocol parameter
    it = gen_text(t)  # tpyc: error(/a 'tuple\[int32, str\]' argument to the generator parameter 'v: Text' is not supported yet; declare the parameter with the argument's type/)
    print([s for s in it])


main()
