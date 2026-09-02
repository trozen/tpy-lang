from tpy import Int32
def main() -> None:
    xs: list[Int32 | None] = [None, None]
    xs[0] = 5
    print(len(xs))
main()
