from tpy import int32
def f(xs: list[int32]) -> int32:
    return sum(x for x in reversed(xs))
def main() -> None:
    print(f([1, 2]))
main()
