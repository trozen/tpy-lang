# divmod() of floats by zero panics with ZeroDivisionError. Pins the
# "float divmod()" message; panic cases are exec-only (no cpy phase), so this
# guards TPy's message text independent of any CPython version.
def main() -> None:
    a: float = 10.0
    b: float = 0.0
    print(divmod(a, b))

main()
