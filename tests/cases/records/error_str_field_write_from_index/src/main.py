# The str field-write row is SLICE-shaped: a single-INDEX subscript is a one-char
# str with its own render, so it must keep rejecting.
class Tag:
    name: str

    def __init__(self) -> None:
        self.name = "t"

    def pick(self, s: str) -> None:
        self.name = s[0]  # tpyc: error(/assign.field_write_shape/)


def main() -> None:
    t = Tag()
    t.pick("abc")
    print(t.name)


main()
