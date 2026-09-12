# A nested def taking a CONTAINER parameter (list / dict / set / bytearray).
# The lambda binds `T&`, so a mutation inside the closure must be visible to
# the caller -- a silent copy would print the pre-call values.
from tpy import Fn, Int32


def apply_fn(f: Fn[[Int32], Int32], v: Int32) -> Int32:
    return f(v)


def push_to(ys: list[Int32], v: Int32) -> Int32:
    ys.append(v)
    return len(ys)


# free function, NON-ESCAPING (`Fn`) slot: a captured container param is bound
# by reference, so the pass-through lambda mutates the CALLER's list. This is
# the sync twin of the frame position in nested_def/lambda_in_gen_method --
# both must print the same list.
def sync_ref_capture(xs: list[Int32]) -> Int32:
    a = apply_fn(lambda v: push_to(xs, v), 9)  # tpyc: ok
    return a + apply_fn(lambda i: xs[i], 2)  # tpyc: ok


def main() -> None:
    def push(xs: list[Int32]) -> None:
        xs.append(9)  # mutates the CALLER's list

    def bump(d: dict[str, Int32]) -> None:
        d["n"] = d["n"] + 1

    def mark(s: set[Int32]) -> None:
        s.add(7)

    def stamp(b: bytearray) -> None:
        b.append(65)

    def total(xs: list[Int32]) -> Int32:
        s = 0
        for x in xs:
            s += x
        return s

    data = [1, 2]
    push(data)  # tpyc: ok
    counts = {"n": 1}
    bump(counts)  # tpyc: ok
    seen = {1}
    mark(seen)  # tpyc: ok
    buf = bytearray()
    stamp(buf)  # tpyc: ok
    print(len(data), data[2], total(data))
    print(counts["n"], len(seen), len(buf))

    src = [1, 2]
    print("lambda_ref", sync_ref_capture(src), src)


main()
