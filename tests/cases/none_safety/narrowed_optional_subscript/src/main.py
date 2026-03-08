# Test subscript, slice, and len() on narrowed Optional[str] values,
# including field access on narrowed optional fields.

class Wrapper:
    text: str | None

    def __init__(self, text: str | None) -> None:
        self.text = text

    def first_char(self) -> None:
        if self.text is not None:
            print(self.text[0])

def check(s: str | None) -> None:
    if s is not None:
        print(s[0])
        print(s[1:4])
        print(len(s))
    else:
        print("none")

def main() -> None:
    check("hello")
    check(None)
    Wrapper("world").first_char()

main()
