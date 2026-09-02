from tpy import Int32, Own, copy
def a(xs: list[Int32], ys: list[Int32], f: bool) -> Own[list[Int32]]:
    return copy(xs) if f else copy(ys)
def main() -> None:
    print(len(a([1], [2], True)))
main()
