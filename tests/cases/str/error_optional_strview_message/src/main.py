# Genuine Optional[A] -> Optional[B] mismatch should include the full declared
# types, not the truncated inner ("expected str | None, got int | None"
# instead of the old "expected str, got int | None").
def takes_str_opt(s: str | None) -> None:
    pass

def returns_int_opt() -> int | None:
    return None

def main() -> None:
    takes_str_opt(returns_int_opt())  # tpyc: error(/expected str \| None, got int \| None/)

main()
