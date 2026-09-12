# A constructor that assigns the same field twice: the member initializer list
# holds one entry per member, so only the first assignment is a member init and
# the second runs in the body over the value the list set.
from tpy import int32

SCALE: int32 = 4


class Acc:
    total: int32
    label: int32

    def __init__(self, base: int32) -> None:
        self.total = base
        self.label = int32(1)
        # The subject: a second assignment to an already-initialized field,
        # reading the value the member init gave it.
        self.total = self.total * SCALE  # tpyc: ok


def main() -> None:
    a = Acc(3)
    print(a.total, a.label)


main()
