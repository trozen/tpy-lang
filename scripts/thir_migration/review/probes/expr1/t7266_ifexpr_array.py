from tpy import Int32, Array
def f(a: Array[Int32, 2], b: Array[Int32, 2], c: bool) -> Int32:
    x = a if c else b
    return x[0]
def main() -> None:
    pass
main()
