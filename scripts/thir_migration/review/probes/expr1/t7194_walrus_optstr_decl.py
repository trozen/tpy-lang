from tpy import int32
def maybe(k: int32) -> str | None:
    return 'x' if k > 0 else None
def f(k: int32) -> str | None:
    x = (s := maybe(k))
    return x
def main() -> None:
    pass
main()
