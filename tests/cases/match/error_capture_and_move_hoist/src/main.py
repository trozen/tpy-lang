# A field capture sharing a target with a last-use assignment must retain
# the capture-write gate; ordinary move support does not admit this hoist.
from tpy import int32, nocopy


@nocopy
class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


class Parcel:
    tag: int32
    item: Cell

    def __init__(self, tag: int32):
        self.tag = tag
        self.item = Cell(5)


def mixed(subject: Parcel) -> int32:
    source = Cell(4)
    # One arm assigns the name and the other captures a field into that name.
    match subject:  # tpyc: error(/match\.field_bind_ptr_hoist/)
        case Parcel(tag=1):
            target = source
        case Parcel(item=target):
            target.value += 10
    target.value += 2
    return target.value


def main() -> None:
    first = Parcel(1)
    second = Parcel(2)
    print(mixed(first), mixed(second), second.item.value)


main()
