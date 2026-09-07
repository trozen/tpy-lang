# `copy()` at a view-family field is an unprobed wrap contract, so the
# constructor rejects rather than guess the render.
from tpy import copy


class Named:
    name: str

    def __init__(self, s: str) -> None:
        self.name = copy(s)  # tpyc: error(/ctor.mil_field.nominal.call/)


def main() -> None:
    print(Named("a").name)


main()
