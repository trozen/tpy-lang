# A member spelled like a variant name its class inherits from a base's
# @dispatch set is refused (docs/LANGUAGE_FEATURES.md, "@dispatch"): it
# would hide the variant from the calls naming it.
from tpy import dispatch, int32


class Base:
    @dispatch
    def m(self, x: int32) -> int32:
        return 1

    @dispatch
    def m(self, x: str) -> int32:
        return 2


class Child(Base):
    def m__int32(self, x: int32) -> int32:  # tpyc: error(/'Child.m__int32' has the same name the compiler gives the inherited variant 'Base.m\(x: int32\)'; rename it/)
        return 99


def main() -> None:
    print(Child().m(3))


main()
