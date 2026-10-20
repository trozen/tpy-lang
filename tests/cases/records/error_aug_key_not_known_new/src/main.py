# An augmented key that must run once but comes from a builtin whose result
# may refer to existing storage cannot be held without a copy; binding it to
# a name first can. (A documented restriction: docs/LANGUAGE_FEATURES.md,
# Assignment.)


def main() -> None:
    names = ["a", "b"]
    it = iter(names)
    d = {"a": 0, "b": 0}
    # `next(it, "a")` advances the iterator, so it runs once; its result is not
    # known to be a new string
    d[next(it, "a")] += 1  # tpyc: error(/the subscript key here is evaluated once but is not known to be a new value, so it cannot be held without a copy; bind it to a name first/)
    print(d)


main()
