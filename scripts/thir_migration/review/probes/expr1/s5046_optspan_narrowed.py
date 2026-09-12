from tpy import int32, Span
def f(sp: Span[int32] | None) -> int32:
    if sp is not None:
        return len(sp)
    return 0
def main() -> None:
    print(f(None))
main()
