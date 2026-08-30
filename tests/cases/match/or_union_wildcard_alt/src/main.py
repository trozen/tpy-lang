# A wildcard or capture alternative makes an or-group over a union subject
# irrefutable, so the alternatives beside it carry no variant label and must
# not be rejected for naming no union member.


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


def feed(a: Dog | Cat | None) -> str:
    match a:
        case Cat() as c:
            # Mutating through the arm binding must reach the caller's object.
            c.hunger -= 1
            return "cat"
        # The wildcard subsumes the alternatives beside it, so the group is the
        # catch-all rather than a per-member dispatch.
        case Dog() | None | _:  # tpyc: ok
            return "rest"
    return "unreached"


def only_wildcard_covers(a: Dog | Fox | None) -> str:
    # Fox is reachable only through the wildcard alternative. Exhaustiveness
    # credits an or-group's named alternatives but not its wildcard, so the
    # match warns even though the group matches everything.
    match a:  # tpyc: warning(/non-exhaustive match on 'None \| Dog \| Fox'; missing: Fox/)
        case Dog() | None | _:
            return "any"
    return "unreached"


def main() -> None:
    d: Dog | Cat | None = Dog(5)
    c: Dog | Cat | None = Cat(7)
    print(feed(d))
    print(feed(c))
    print(feed(None))
    print(c.hunger)
    print(only_wildcard_covers(Fox(3)))
    print(only_wildcard_covers(None))


main()
