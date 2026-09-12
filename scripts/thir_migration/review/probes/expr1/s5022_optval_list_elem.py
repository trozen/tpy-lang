from tpy import int32
def f(x: int32 | None) -> int32:
    xs = [x]
    return len(xs)
def main() -> None:
    print(f(1))
main()
