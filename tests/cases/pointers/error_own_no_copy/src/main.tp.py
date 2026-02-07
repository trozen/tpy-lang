from tpy import Int32, Own


class Box:
    value: Int32


def bad_return(b: Box) -> Own[Box]:
    return b  # tpyc: error(/explicit copy/)


def main() -> None:
    pass


main()
