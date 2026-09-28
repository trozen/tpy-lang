# A record `__getitem__` value read through an un-narrowed Optional receiver
# rejects, located (BUGS.md#unproven-optional-elem-sink-rejects).
class R:
    v: int

    def __init__(self) -> None:
        self.v = 7

    def __getitem__(self, i: int) -> int:
        return self.v + i


def f(r: R | None) -> None:
    # the subject: the record getitem off the unproven receiver
    print(r[1])  # tpyc: error(/not yet supported.*subscript.optional_check/)


def main() -> None:
    f(R())


main()
