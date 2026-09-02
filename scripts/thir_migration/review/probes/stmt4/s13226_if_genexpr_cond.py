from tpy import Int32
def f(xs: list[Int32]) -> Int32:
    if sum(v for v in reversed(xs)) > 2:
        return 1
    return 0
def main() -> None:
    print(f([1, 2]))
main()
