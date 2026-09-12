from tpy import int32
def f(d: list[int32] | None) -> int32:
    return d[0]
def main() -> None:
    print(f([1]))
main()
