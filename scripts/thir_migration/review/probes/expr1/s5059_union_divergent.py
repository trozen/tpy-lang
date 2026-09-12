from tpy import int32
def f(k: int32) -> int32:
    x: int32 | float = k
    v = x
    return v
def main() -> None:
    print(f(1))
main()
