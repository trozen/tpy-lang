from tpy import Int32
def f(a: list[Int32], b: list[Int32], c: bool) -> Int32:
    return (a if c else b)[0]
def main() -> None:
    pass
main()
