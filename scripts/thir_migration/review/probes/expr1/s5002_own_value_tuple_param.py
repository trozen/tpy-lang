from tpy import int32, Own
def f(p: Own[tuple[int32, int32]]) -> int32:
    return p[0]
def main() -> None:
    print(f((1, 2)))
main()
