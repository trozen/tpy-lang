from tpy import int32, Array
def f(a: Array[int32, 2], b: Array[int32, 2], c: bool) -> int32:
    x = a if c else b
    return x[0]
def main() -> None:
    pass
main()
