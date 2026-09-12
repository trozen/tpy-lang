from tpy import int32, Span
def f(sp: Span[int32] | None) -> int32:
    xs = [sp]
    return len(xs)
def main() -> None:
    print(f(None))
main()
