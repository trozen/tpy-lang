from tpy import Int32, Int64
type NUM = Int32 | Int64
def f(x: NUM) -> Int32:
    if isinstance(x, Int32):
        x += 1
        return x
    return 0
def main() -> None:
    print(f(3))
main()
