# BaseN.method(self, ...) disambiguates calls to a specific ancestor's method.
# Each base defines its own 'describe' variant; the child overrides to satisfy
# the cross-base conflict check but still dispatches to each base statically
# via BaseN.describe(self).


class Left:
    def describe(self) -> str:
        return "left"


class Right:
    def describe(self) -> str:
        return "right"


class Both(Left, Right):
    def describe(self) -> str:
        # Override required by the cross-base conflict check; delegate to
        # each base explicitly via the unbound-self form.
        left = Left.describe(self)
        right = Right.describe(self)
        return left + "+" + right


def main() -> None:
    b = Both()
    print(b.describe())


main()
