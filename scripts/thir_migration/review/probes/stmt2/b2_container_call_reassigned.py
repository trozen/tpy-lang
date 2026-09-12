from tpy import int32, Own
def mk(n: int32) -> Own[list[int32]]:
    return [n]
def main() -> None:
    xs = mk(1)
    xs = mk(2)
    print(len(xs))
main()
