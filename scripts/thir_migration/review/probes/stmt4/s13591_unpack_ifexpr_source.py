from tpy import int32
def f(c: bool, t1: tuple[int32, int32], t2: tuple[int32, int32]) -> int32:
    a, b = t1 if c else t2
    return a + b
def main() -> None:
    print(f(True, (1, 2), (3, 4)))
main()
