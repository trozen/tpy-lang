from tpy import int32
def src() -> tuple[int32, str]:
    return (1, 'a')
def a(f: bool) -> tuple[int32, str]:
    t = (1, 'a')
    u = (2, 'b')
    return t if f else u
def main() -> None:
    print(a(True)[0])
main()
