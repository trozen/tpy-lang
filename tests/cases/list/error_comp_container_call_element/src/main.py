# A bare NAME element at a container slot lowers, but a CALL element there must
# keep rejecting: the container-slot render is element-shape-sensitive and a
# call result has no vetted spelling at that slot.
from tpy import int32, Own


def mk() -> Own[list[int32]]:
    return [1, 2]


def main() -> None:
    ls: list[list[int32]] = [mk() for i in range(2)]  # tpyc: error(/expr.list_comp/)
    print(len(ls))


main()
