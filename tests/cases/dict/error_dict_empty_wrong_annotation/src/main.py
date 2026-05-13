# Empty {} with a non-dict annotation is rejected; pins the type-mismatch
# wording produced by the LHS-hint propagation path (regression guard).
def main() -> None:
    d: int = {}  # tpyc: error(/Type mismatch in variable 'd': expected int, got PendingDict/)


main()
