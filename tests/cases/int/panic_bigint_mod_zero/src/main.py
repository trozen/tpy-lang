# BigInt modulo by zero panics with ZeroDivisionError. Pins the message
# wording; panic cases are exec-only (no cpy phase), so this guards TPy's
# message text independent of any CPython version.
def main() -> None:
    a: int = 10
    b: int = 0
    print(a % b)

main()
