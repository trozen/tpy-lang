from tpy import Int32
def f(xs: list[Int32]) -> None:
    print(sum(v for v in reversed(xs)))
def main() -> None:
    f([1, 2])
main()
