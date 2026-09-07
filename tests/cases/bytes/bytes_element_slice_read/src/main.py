# Slicing a bytes element of a container: the chained element read is the
# receiver of the slice, so the outer index applies to the element, not the list.
def peek(app: list[bytes]) -> None:
    print(len(app[0][1:]))


def main() -> None:
    xs: list[bytes] = [b"hi"]
    peek(xs)


main()
