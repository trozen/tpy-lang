from tpy import int32, Own, copy
def a(xs: list[int32], ys: list[int32], f: bool) -> Own[list[int32]]:
    return copy(xs) if f else copy(ys)
def main() -> None:
    print(len(a([1], [2], True)))
main()
