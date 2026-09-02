from tpy import Int32
def f(x: str | list[Int32]) -> Int32:
    if isinstance(x, str):
        return len(x[0])
    return x[0]
def main() -> None:
    print(f('ab'))
main()
