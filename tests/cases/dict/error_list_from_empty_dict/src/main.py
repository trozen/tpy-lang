# Regression: list({}) must emit a user-facing diagnostic, not an
# internal type-param lookup error ("Unknown type parameter 'K'").
# Element type is unresolvable from an empty dict.
def main() -> None:
    d = list({})  # tpyc: error(/Cannot infer element type for empty dict passed to list/)
    print(len(d))
main()
