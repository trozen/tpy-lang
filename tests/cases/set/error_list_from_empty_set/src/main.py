# Regression: list(set()) must emit a user-facing diagnostic, not an
# internal compiler error ("UnknownElementType should be resolved before
# codegen"). Element type is unresolvable from an empty set.
def main() -> None:
    d = list(set())  # tpyc: error(/Cannot infer element type for empty set passed to list/)
    print(len(d))
main()
