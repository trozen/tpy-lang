from tpy import Int32
def f(xs: list[Int32]) -> Int32:
    return sum(x for x in reversed(xs))
def main() -> None:
    print(f([1, 2]))
main()
