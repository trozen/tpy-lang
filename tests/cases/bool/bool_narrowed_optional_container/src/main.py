# A pointer-repr Optional[container] (list/dict/set) narrowed past None and
# held in a T* name dispatches emptiness (CPython's len-based truthiness), not
# the always-true non-null pointer test. This rides the same narrowed-swap the
# Optional[record] fix uses (the swap is not record-only), so it is guarded
# here against a future refactor silently regressing it. The un-narrowed
# container form is a separate, still-open shape (see BUGS.md).


def narrowed_list(xs: list[str] | None) -> int:
    if xs is None:
        return -1
    if xs:
        return 1
    return 0


def narrowed_dict(d: dict[str, str] | None) -> int:
    if d is None:
        return -1
    if d:
        return 1
    return 0


def narrowed_set(s: set[str] | None) -> int:
    if s is None:
        return -1
    if s:
        return 1
    return 0


def main():
    empty_list: list[str] = []
    empty_dict: dict[str, str] = {}
    empty_set: set[str] = set()
    print(narrowed_list(empty_list), narrowed_list(["a"]))
    print(narrowed_dict(empty_dict), narrowed_dict({"k": "v"}))
    print(narrowed_set(empty_set), narrowed_set({"x"}))


main()
