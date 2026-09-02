from tpy import Int32
def a[T](xs: list[tuple[T, Int32]]) -> tuple[T, Int32]:
    return xs[0]
def main() -> None:
    xs: list[tuple[Int32, Int32]] = [(1, 2)]
    print(a(xs)[1])
main()
