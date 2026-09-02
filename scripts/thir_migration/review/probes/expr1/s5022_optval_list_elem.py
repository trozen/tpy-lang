from tpy import Int32
def f(x: Int32 | None) -> Int32:
    xs = [x]
    return len(xs)
def main() -> None:
    print(f(1))
main()
