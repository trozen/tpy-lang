from tpy import Int32, Own
def mk(n: Int32) -> Own[list[Int32]]:
    return [n]
def main() -> None:
    xs = mk(1)
    xs = mk(2)
    print(len(xs))
main()
