# Pins the restriction in docs/LANGUAGE_FEATURES.md "F-string Formatting"
# (format specs): a spec on a union formats its str() text, so a number's
# spec (`f`, a sign, `#`, the `0` flag) is a compile error there.


def show(u: float | str) -> str:
    return f"[{u:.2f}]"  # tpyc: error(/Format spec ':.2f' cannot apply to text: 'f' is a number's presentation type/)


def main() -> None:
    print(show(3.14159))


main()
