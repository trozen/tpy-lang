from tpy import Int32
def g(k: Int32) -> Int32 | None:
    return k if k > 0 else None
def f(k: Int32) -> Int32:
    return -g(k)
def main() -> None:
    pass
main()
