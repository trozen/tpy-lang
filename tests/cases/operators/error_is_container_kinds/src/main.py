# `is` between containers of two kinds (a list and a dict) is rejected as
# unrelated types (stricter by design: docs/LANGUAGE_FEATURES.md, Operators /
# Comparison); containers of one kind compare whatever their element types.
def same(xs: list[int], d: dict[str, int]) -> bool:
    return xs is d  # tpyc: error(/'list\[int\]' and 'dict\[str, int\]' are unrelated \(neither is a subclass of the other\)$/)


print(same([1], {"k": 1}))
