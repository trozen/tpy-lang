# A comprehension at a method slot the callee MUTATES: the inline
# statement-expression is a prvalue and a mutated slot emits `T&`, which no
# rvalue can bind -- so the shape must keep rejecting rather than emit C++
# that does not compile.


class Sink:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def soak(self, row: list[float]) -> None:
        row.append(1.0)
        self.n = len(row)


def main() -> None:
    s = Sink()
    s.soak([2.0 * float(i) for i in range(3)])  # tpyc: error(/method.arg_shape/)
    print(s.n)


main()
