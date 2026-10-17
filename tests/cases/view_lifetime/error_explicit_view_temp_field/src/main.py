# An explicit view of a temporary record's field dangles once the record dies
# (LANGUAGE_FEATURES "StrView is a non-owning view").
from tpy import Own, StrView


class Rec:
    def __init__(self, name: str) -> None:
        self.name = name


def make(n: int) -> Own[Rec]:
    return Rec("record-" + str(n))


def main() -> None:
    v: StrView = make(7).name  # tpyc: error(/Cannot bind StrView 'v' to a temporary view source/)
    print(v)


main()
