# A `String` constructor param appended to inside the constructor body: the
# param is a const reference there, so the constructor rejects.
from tpy import Int32, String


class C:
    y: Int32

    def __init__(self, p: String) -> None:  # tpyc: error(/ctor\.param_mutated_string/)
        self.y = 1
        p += "!"
        print(p)


def main() -> None:
    s = String("a")
    C(s)


main()
