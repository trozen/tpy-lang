from tpy import Int32
def maybe(k: Int32) -> str | None:
    return 'x' if k > 0 else None
def f(k: Int32) -> str | None:
    x = (s := maybe(k))
    return x
def main() -> None:
    pass
main()
