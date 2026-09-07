# A `str` (view) source at a constructor parameter the body MUTATES: the
# view arrives through a coerce temp, which cannot bind the mutable slot.
from tpy import Own, String


class S:
    name: String

    def __init__(self, name: String) -> None:
        name += "!"
        self.name = name


def use(s: str) -> Own[S]:
    # The view source lands at a mutated String parameter.
    return S(s)  # tpyc: error(/expr\.call:call\.ctor_arg\.str/)


def main() -> None:
    print(use("a").name)


main()
