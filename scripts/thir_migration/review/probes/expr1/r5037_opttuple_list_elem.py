from tpy import Int32
def f(tp: tuple[Int32, Int32] | None) -> Int32:
    xs = [tp]
    return len(xs)
def main() -> None:
    pass
main()
