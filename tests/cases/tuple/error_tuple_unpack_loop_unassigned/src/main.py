# Error: a tuple-unpack target assigned only inside a loop body (which may run
# zero times) and read after the loop is rejected by definite-assignment,
# like the scalar `while ...: x = ...; print(x)` case. TPy is intentionally
# stricter here than CPython (which accepts it, raising UnboundLocalError only
# if the loop ran zero times).
def split2(s: str) -> tuple[str, str]:
    n = len(s) // 2
    return (s[:n], s[n:])


def main() -> None:
    s = "abcdefghijklmnop"
    i = 0
    while i < 3:
        s, kept = split2(s)
        i = i + 1
    print(kept)  # tpyc: error(/variable 'kept' may not be assigned/)


main()
