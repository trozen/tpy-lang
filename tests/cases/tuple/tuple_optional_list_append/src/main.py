# list[tuple[T | None, ...]].append() of an rvalue tuple lifts
# pointer-form -> storage-form via tuple_to_storage in gen_call_arg.
# Source elements are mutable lvalue locals; const-source paths surface
# the separate const-iteration bug tracked in BUGS.md.
from tpy import Int32


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def main() -> None:
    a = P(1)
    b = P(2)

    pairs: list[tuple[P | None, P | None]] = []
    pairs.append((a, b))
    pairs.append((a, None))
    pairs.append((None, b))
    pairs.append((None, None))
    print(len(pairs))

    # Storage-form source (subscript) into append -- must not double-wrap.
    pairs2: list[tuple[P | None, P | None]] = []
    pairs2.append(pairs[0])
    pairs2.append(pairs[1])
    print(len(pairs2))

    # Read back through pointer-form param to verify storage works.
    a0, b0 = pairs[0]
    if a0 is not None:
        print(a0.x)
    if b0 is not None:
        print(b0.x)
    a1, b1 = pairs[1]
    if a1 is not None:
        print(a1.x)
    if b1 is None:
        print("b1=None")


main()
