from tpy import int32
def main() -> None:
    xs: list[int32 | None] = [None, None]
    xs[0] = 5
    print(len(xs))
main()
