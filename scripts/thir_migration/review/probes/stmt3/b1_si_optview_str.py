from tpy import int32
def main() -> None:
    xs: list[str | None] = [None, None]
    xs[0] = 'hi'
    print(len(xs))
main()
