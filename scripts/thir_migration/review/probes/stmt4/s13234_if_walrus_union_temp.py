from tpy import Int32
def take(v: Int32 | str) -> Int32:
    return 1
def g(n: Int32) -> Int32:
    return n + 1
def f(n: Int32) -> Int32:
    if (m := g(n)) > take(n):
        return m
    return 0
def main() -> None:
    print(f(1))
main()
