from tpy import Int32
def go(u: list[tuple[Int32, Int32]] | None) -> Int32:
    t = 0
    if u is not None:
        for a, b in u:
            t += a + b
    return t
def main() -> None:
    print(go([(1, 2), (3, 4)]))
main()
