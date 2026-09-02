from tpy import Int32, StrView
def f(k: Int32) -> Int32:
    v: StrView | None = None
    if k > 0:
        v = 'abc'
    if v is not None:
        return len(v)
    return 0
def main() -> None:
    pass
main()
