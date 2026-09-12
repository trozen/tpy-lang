from tpy import int32, Span, Ptr
def f(p: Ptr[int32]) -> int32:
    sp = Span[int32](p, 3)
    return len(sp)
def main() -> None:
    pass
main()
