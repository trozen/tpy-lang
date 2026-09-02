from tpy import Int32
def pick(c: bool) -> Int32:
    if c:
        s: str | None = "a"
    else:
        s = None
    if s is None:
        return 0
    return len(s)
def main() -> None:
    print(pick(True))
main()
