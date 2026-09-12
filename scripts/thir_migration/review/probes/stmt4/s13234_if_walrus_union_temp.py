from tpy import int32
def take(v: int32 | str) -> int32:
    return 1
def g(n: int32) -> int32:
    return n + 1
def f(n: int32) -> int32:
    if (m := g(n)) > take(n):
        return m
    return 0
def main() -> None:
    print(f(1))
main()
