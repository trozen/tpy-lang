from tpy import int32
def go(u: list[tuple[int32, int32]] | int32) -> int32:
    t = 0
    if isinstance(u, list):
        for a, b in u:
            t += a + b
    return t
def main() -> None:
    print(go([(1, 2), (3, 4)]))
main()
