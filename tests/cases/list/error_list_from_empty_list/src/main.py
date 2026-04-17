# Regression: list([]) must emit the empty-container-specific diagnostic,
# not the generic "Cannot infer type parameters" fallback. Element type
# is unresolvable from an empty list literal.
def main() -> None:
    d = list([])  # tpyc: error(/Cannot infer element type for empty list passed to list/)
    print(len(d))
main()
