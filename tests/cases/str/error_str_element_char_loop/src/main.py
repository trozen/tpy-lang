# A char loop over a str ELEMENT is not a container element read, so the
# element leg's own reject falls the loop back.
def main() -> None:
    xs: list[str] = ["ab"]
    for c in xs[0]:  # tpyc: error(/foreach.subscript_elem_family/)
        print(c)


main()
