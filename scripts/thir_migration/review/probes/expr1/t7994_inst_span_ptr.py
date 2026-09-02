from tpy import Int32, Span, Ptr
def f(p: Ptr[Int32]) -> Int32:
    sp = Span[Int32](p, 3)
    return len(sp)
def main() -> None:
    pass
main()
