from tpy import int32
def main() -> None:
    xs: list[int32 | None] = [1, None]
    print(sum(1 for x in xs if x is not None))
main()
