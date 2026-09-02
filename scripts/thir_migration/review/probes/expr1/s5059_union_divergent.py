from tpy import Int32
def f(k: Int32) -> Int32:
    x: Int32 | float = k
    v = x
    return v
def main() -> None:
    print(f(1))
main()
