from tpy import Int32
def take(u: Int32 | str) -> bool:
    return isinstance(u, Int32)
def f(flag: bool, x: Int32) -> bool:
    return flag and take(x)
def main() -> None:
    print(f(True, 1))
main()
