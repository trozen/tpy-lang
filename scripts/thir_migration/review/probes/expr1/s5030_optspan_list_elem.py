from tpy import Int32, Span
def f(sp: Span[Int32] | None) -> Int32:
    xs = [sp]
    return len(xs)
def main() -> None:
    print(f(None))
main()
