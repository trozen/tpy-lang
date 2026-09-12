from tpy import int32
def take(v: int32 | str) -> int32:
    return 1
def g(n: int32) -> int32:
    return n + 1
def f(n: int32) -> int32:
    t = int32(0)
    while (m := g(n)) > take(t):
        t += m
        n -= 1
    return t
def main() -> None:
    print(f(1))
main()
