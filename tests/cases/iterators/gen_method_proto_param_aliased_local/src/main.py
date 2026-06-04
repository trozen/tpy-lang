# Generator-METHOD sibling of gen_proto_param_aliased_local: exercises the
# method-side forwarding-detection + the method coro-struct (captured `__self`
# plus the deduced template arg T_it). The static-protocol param is aliased
# into a local then iterated across yields; the alias forwards to the captured
# param, reusing T_it. Borrow-forcing: the source is mutated before iteration
# and the appended element is observed (a copy would miss it). NB: method
# generators don't yet emit the `while borrowed` lint that the free-function
# form does (pre-existing borrow-tracker gap, BUGS.md) -- the borrow itself is
# sound, so output parity is the value-vs-reference guard here.
from typing import Iterator, Iterable
from tpy import Int32


class Repeater:
    times: Int32

    def __init__(self, times: Int32) -> None:
        self.times = times

    def run(self, it: Iterable[Int32]) -> Iterator[Int32]:
        xs = it
        for x in xs:
            for _ in range(self.times):
                yield x


def main() -> None:
    data = [1, 2]
    g = Repeater(2).run(data)
    data.append(3)
    for v in g:
        print(v)


main()
