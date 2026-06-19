# A str tuple-unpack target reassigned in a loop (head, tail = split(head)) owns
# its element -- a string_view into the per-iteration tuple temp would dangle.
def split2(s: str) -> tuple[str, str]:
    n = len(s) // 2
    return (s[:n], s[n:])


def main() -> None:
    head = "abcdefghijklmnop"
    acc = 0
    while len(head) > 1:
        head, tail = split2(head)
        acc = acc + len(tail)
    print(acc)
    print(head)


main()
