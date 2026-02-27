# Test __repr__ fallback: str()/print()/f-string fall back to __repr__ when no __str__
class Tag:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def __repr__(self) -> str:
        return "Tag(" + self.label + ")"

def main() -> None:
    t: Tag = Tag("hello")
    print(repr(t))
    print(f"{t!r}")
    print(t)
    print(str(t))
    print(f"{t}")
    print(f"{t!s}")

main()
