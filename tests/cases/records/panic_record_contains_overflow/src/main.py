# A BigInt needle beyond a user __contains__'s declared Int32 param width
# takes the checked call-arg narrow and panics (the declared-param
# convention; CPython would call the method with the big int).
from tpy import Int32


class Bag:
    xs: list[Int32]

    def __init__(self):
        self.xs = [1, 2]

    def __contains__(self, item: Int32) -> bool:
        return item in self.xs


def main():
    b = Bag()
    k: int = 2**40
    print(k in b)


main()
