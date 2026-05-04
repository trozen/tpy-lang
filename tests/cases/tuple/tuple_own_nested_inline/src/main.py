# Inline-nested form: outer literal nests two inner literals, each of which
# directly consumes its `@nocopy` locals. _gen_tuple_literal recurses through
# elem_target so each level emits std::move correctly. This is the recommended
# workaround for the "ref-tuple of @nocopy" sema reject.
from tpy import nocopy, Int32, Own


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32) -> None:
        self.fd = fd


def two_pairs() -> tuple[tuple[Own[Handle], Own[Handle]], tuple[Own[Handle], Own[Handle]]]:
    a = Handle(Int32(1))
    b = Handle(Int32(2))
    c = Handle(Int32(3))
    d = Handle(Int32(4))
    return ((a, b), (c, d))


def main() -> None:
    pp = two_pairs()
    print(pp[0][0].fd)
    print(pp[0][1].fd)
    print(pp[1][0].fd)
    print(pp[1][1].fd)


main()
