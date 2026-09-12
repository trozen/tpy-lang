from tpy import int32
def take(u: int32 | str) -> bool:
    return isinstance(u, int32)
def f(flag: bool, x: int32) -> bool:
    return flag and take(x)
def main() -> None:
    print(f(True, 1))
main()
