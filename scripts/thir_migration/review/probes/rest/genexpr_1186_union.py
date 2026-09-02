from tpy import Int32
def f(xs: list[Int32] | str) -> Int32:
    if isinstance(xs, list):
        return sum(x for x in xs)
    return 0
def main() -> None:
    print(f([1, 2]))
main()
