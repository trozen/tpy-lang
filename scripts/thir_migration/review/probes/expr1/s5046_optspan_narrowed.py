from tpy import Int32, Span
def f(sp: Span[Int32] | None) -> Int32:
    if sp is not None:
        return len(sp)
    return 0
def main() -> None:
    print(f(None))
main()
