# A comprehension IS the member-init value: the cell spells its
# statement-expression verbatim, with no body demotion and no temp.
from tpy import int32


class Rows:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [i for i in range(3)]  # spelled into the init list


def main() -> None:
    a = Rows()
    print(len(a.items), a.items[2])


main()
