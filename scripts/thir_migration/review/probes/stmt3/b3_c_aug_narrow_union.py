from tpy import int32, int64
type NUM = int32 | int64
def f(x: NUM) -> int32:
    if isinstance(x, int32):
        x += 1
        return x
    return 0
def main() -> None:
    print(f(3))
main()
