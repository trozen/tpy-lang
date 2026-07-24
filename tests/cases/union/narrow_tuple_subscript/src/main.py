# A literal-index subscript of a tuple narrowed out of a union lowers to
# std::get<N> (the tuple fast-path keys on the narrowed member, not the declared
# union -- which would emit ill-formed operator[] on std::tuple).
from tpy import Own


class Rec:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n


def pick(flag: bool) -> Own[tuple[str, int] | Rec]:
    if flag:
        return ("hi", 7)
    return Rec(3)


def main() -> None:
    v = pick(True)
    if isinstance(v, Rec):
        print(v.n)
    else:
        print(v[0], v[1])  # std::get<0>, std::get<1>


main()
