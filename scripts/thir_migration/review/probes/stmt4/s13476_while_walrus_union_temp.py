from tpy import Int32
def take(v: Int32 | str) -> Int32:
    return 1
def g(n: Int32) -> Int32:
    return n + 1
def f(n: Int32) -> Int32:
    t = Int32(0)
    while (m := g(n)) > take(t):
        t += m
        n -= 1
    return t
def main() -> None:
    print(f(1))
main()
