# A container FIELD read at a builtin-stub method's structural Iterable slot.
# The stub receiver families share one arg-row list, so one field row serves a
# container receiver and a str-view receiver alike.
from tpy import int32


class Holder:
    nums: list[int32]
    tags: set[int32]
    parts: list[str]
    ages: dict[str, int32]

    def __init__(self) -> None:
        self.nums = [1, 2]
        self.tags = {7}
        self.parts = ["a", "b"]
        self.ages = {"a": 1}


def main() -> None:
    h = Holder()
    out: list[int32] = []
    out.extend(h.nums)  # tpyc: ok
    # The field is READ, not consumed: appending to it after the call must
    # show up in a second extend, and `out` must not alias it.
    h.nums.append(3)
    out.extend(h.nums)  # tpyc: ok
    print(out)
    print(h.nums)

    # ... a SET field at the same slot.
    from_set: list[int32] = []
    from_set.extend(h.tags)  # tpyc: ok
    print(from_set)

    # The view receiver's own field row, unchanged -- the leg that pins the
    # two families really do share the row.
    print(",".join(h.parts))  # tpyc: ok

    # A bytearray receiver at the same slot.
    buf = bytearray(b"z")
    buf.extend(h.nums)  # tpyc: ok
    print(len(buf))

    # ... and the CONCRETE container slot (`dict.update` / `set.update` take
    # a dict / set, not a structural Iterable): the field read binds it
    # exactly as the bare name does.
    d: dict[str, int32] = {}
    d.update(h.ages)  # tpyc: ok
    st: set[int32] = set()
    st.update(h.tags)  # tpyc: ok
    # update COPIES the entries, in TPy as in CPython: writing the field
    # afterwards leaves the updated containers alone.
    h.ages["b"] = 2
    h.tags.add(8)
    print(len(d), len(st), len(h.ages), len(h.tags))


main()
