# A subclass omitting a pointer-repr union base-init arg: the AST renders the
# omitted None as `{}`, so THIR must not reproduce it as a bare `nullptr`.

from tpy import Int64


class Cat:
    def __init__(self, n: Int64) -> None:
        self.n = n


class Dog:
    def __init__(self, n: Int64) -> None:
        self.n = n


class Base:
    tag: Int64
    has_pet: bool

    # `Cat | Dog | None` is a POINTER-repr union, whose None still spells `{}`
    # (monostate is the first alternative in both reprs), not `nullptr`.
    def __init__(self, tag: Int64, pet: Cat | Dog | None = None) -> None:
        self.tag = tag
        self.has_pet = pet is not None


class Sub(Base):
    def __init__(self, tag: Int64) -> None:
        # The subject: THIR's base-init arg render is target-less, so it can only
        # reproduce a bare `nullptr` -- this pairing must reject and fall back.
        super().__init__(tag, None)


def main() -> None:
    s = Sub(3)
    print(s.tag)
    print(s.has_pet)


main()
