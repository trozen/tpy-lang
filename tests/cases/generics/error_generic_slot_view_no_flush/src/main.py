# A view-form `str` at a generic `T` slot needs a materialized owned temp, and a
# while condition has no statement to flush one into -- so the call rejects with
# a located message rather than rendering the un-copied view into a slot spelled
# off the owning type. The flushable positions are the happy-path case
# `generics/generic_slot_view_form_positions`.
# This REJECTS valid Python; the divergence is the documented one in
# docs/LANGUAGE_FEATURES.md ("no statement to hoist the temp into").
def has_item[T](xs: list[T], v: T) -> bool:
    for x in xs:
        if x == v:
            return True
    return False


def main(k: str) -> None:
    names = ["a", "b"]
    n = 0
    # The subject: the generic slot's temp cannot be hoisted here.
    while has_item(names, k) and n < 1:  # tpyc: error(/not yet supported/)
        n += 1
    print(n)


main("a")
