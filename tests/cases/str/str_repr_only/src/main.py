# Test __repr__ only (without __str__): repr() works, print() uses field dump
# Divergence from Python: Python's str() falls back to __repr__, tpyc does not
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
    # print() uses auto-generated field dump (no __str__)
    print(t)

main()
