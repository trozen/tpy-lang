from tpy import Int32
def f(c: bool, t1: tuple[Int32, Int32], t2: tuple[Int32, Int32]) -> Int32:
    a, b = t1 if c else t2
    return a + b
def main() -> None:
    print(f(True, (1, 2), (3, 4)))
main()
