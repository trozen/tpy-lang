# BaseN.field resolves through BaseN's own MRO when BaseN doesn't declare
# the field directly. Mirrors the v2.1 method-lookup semantics: `B.foo(self)`
# works even if B inherits foo from an ancestor.
from tpy import int32


class Root:
    token: int32


class Middle(Root):
    pass


class Other:
    token: str


class Leaf(Middle, Other):
    def __init__(self) -> None:
        Middle.token = 10  # resolves to Root.token through Middle's MRO
        Other.token = "hi"

    def as_pair(self) -> str:
        return Other.token + "/" + str(Middle.token)


def main() -> None:
    print(Leaf().as_pair())


main()
