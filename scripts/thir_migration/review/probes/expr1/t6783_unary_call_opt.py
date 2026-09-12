from tpy import int32
def g(k: int32) -> int32 | None:
    return k if k > 0 else None
def f(k: int32) -> int32:
    return -g(k)
def main() -> None:
    pass
main()
