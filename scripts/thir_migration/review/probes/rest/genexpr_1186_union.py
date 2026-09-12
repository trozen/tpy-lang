from tpy import int32
def f(xs: list[int32] | str) -> int32:
    if isinstance(xs, list):
        return sum(x for x in xs)
    return 0
def main() -> None:
    print(f([1, 2]))
main()
