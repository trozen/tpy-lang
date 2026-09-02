from tpy import Int32
def f(d: list[Int32] | None) -> Int32:
    return d[0]
def main() -> None:
    print(f([1]))
main()
