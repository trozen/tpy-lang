# A bare NAME element at a container slot lowers, but a CALL element there must
# keep rejecting: the container-slot render is element-shape-sensitive and a
# call result has no vetted spelling at that slot.
from tpy import Int32, Own


def mk() -> Own[list[Int32]]:
    return [1, 2]


def main() -> None:
    ls: list[list[Int32]] = [mk() for i in range(2)]  # tpyc: error(/expr.list_comp/)
    print(len(ls))


main()
