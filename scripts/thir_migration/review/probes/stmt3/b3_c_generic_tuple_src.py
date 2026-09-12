from tpy import int32
def a[T](xs: list[tuple[T, int32]]) -> tuple[T, int32]:
    return xs[0]
def main() -> None:
    xs: list[tuple[int32, int32]] = [(1, 2)]
    print(a(xs)[1])
main()
