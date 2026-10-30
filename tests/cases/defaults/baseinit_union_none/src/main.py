# A `None` base-init argument at a pointer-repr union parameter: it is built
# at that parameter as the union's empty alternative, never a bare `nullptr`.

from tpy import int64


class Cat:
    def __init__(self, n: int64) -> None:
        self.n = n


class Dog:
    def __init__(self, n: int64) -> None:
        self.n = n


class Base:
    tag: int64
    has_pet: bool

    # `Cat | Dog | None` is a POINTER-repr union, whose None still spells `{}`
    # (monostate is the first alternative in both reprs), not `nullptr`.
    def __init__(self, tag: int64, pet: Cat | Dog | None = None) -> None:
        self.tag = tag
        self.has_pet = pet is not None


class Sub(Base):
    def __init__(self, tag: int64) -> None:
        # The subject: `None` converts at the base's union parameter.
        super().__init__(tag, None)  # tpyc: ok


def main() -> None:
    s = Sub(3)
    print(s.tag)
    print(s.has_pet)


main()
