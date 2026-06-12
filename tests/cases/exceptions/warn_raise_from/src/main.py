# 'raise ... from cause' warns: TPy's exception model has no __cause__,
# so the chaining clause is dropped (declared divergence).
def main() -> None:
    try:
        try:
            raise ValueError("inner")
        except ValueError as e:
            raise RuntimeError("outer") from e  # tpyc: warning(/exception chaining .* is not modeled/)
    except RuntimeError as r:
        print("caught", r)
    try:
        try:
            raise ValueError("x")
        except ValueError:
            raise RuntimeError("clean") from None  # tpyc: warning(/context suppression .* is not modeled/)
    except RuntimeError as r2:
        print("caught", r2)


main()
