from tpy import int32
def f(x: str | list[int32]) -> int32:
    if isinstance(x, str):
        return len(x[0])
    return x[0]
def main() -> None:
    print(f('ab'))
main()
