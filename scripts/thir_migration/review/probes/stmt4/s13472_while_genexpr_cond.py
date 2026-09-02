from tpy import Int32
def f(xs: list[Int32]) -> Int32:
    t = Int32(0)
    while sum(v for v in reversed(xs)) > t:
        t += 1
    return t
def main() -> None:
    print(f([1, 2]))
main()
