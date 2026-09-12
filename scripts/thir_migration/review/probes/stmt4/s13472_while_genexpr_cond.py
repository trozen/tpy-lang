from tpy import int32
def f(xs: list[int32]) -> int32:
    t = int32(0)
    while sum(v for v in reversed(xs)) > t:
        t += 1
    return t
def main() -> None:
    print(f([1, 2]))
main()
