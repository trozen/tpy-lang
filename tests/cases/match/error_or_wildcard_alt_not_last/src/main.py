# A wildcard alternative makes an or-group over a union subject irrefutable, so
# every arm after it is dead. The group lowers to the switch default and a C++
# switch ignores source order, so admitting the later arms would leave them live
# and silently pick them over the earlier group. CPython rejects the same
# program with "wildcard makes remaining patterns unreachable".


class Dog:
    hunger: int

    def __init__(self, hunger: int) -> None:
        self.hunger = hunger


class Cat:
    hunger: int

    def __init__(self, hunger: int) -> None:
        self.hunger = hunger


class Fox:
    hunger: int

    def __init__(self, hunger: int) -> None:
        self.hunger = hunger


def pick(x: Dog | Cat | Fox | None) -> str:
    match x:
        case Dog() | None | _:
            return "rest"
        case Cat():  # tpyc: error(/unreachable case after wildcard pattern/)
            return "cat"
        case Fox():
            return "fox"
    return "no"


def main() -> None:
    print(pick(Cat(1)))


main()
