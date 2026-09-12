from tpy import int32
def f(a: list[int32], b: list[int32], c: bool) -> int32:
    return (a if c else b)[0]
def main() -> None:
    pass
main()
