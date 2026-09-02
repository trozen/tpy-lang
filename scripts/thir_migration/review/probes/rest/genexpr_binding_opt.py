from tpy import Int32
def main() -> None:
    xs: list[Int32 | None] = [1, None]
    print(sum(1 for x in xs if x is not None))
main()
