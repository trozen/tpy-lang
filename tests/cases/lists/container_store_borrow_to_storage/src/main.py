# Storing a borrow-form value (pointer-repr Optional / pointer-variant
# Union param) into a storage-form container element must lift borrow->storage
# (ptr_to_optional / to_value_variant). Covers xs[i] = p (Optional + Union) and
# xs.append(p) (Union); pre-fix each emitted ill-formed C++ (optional/variant
# vs T*/variant-of-ptr) with no diagnostic.
#
# Copy-into-storage is intended at the element-store boundary (a container
# element owns its data inline) -- TPy copies with a warning where CPython
# would alias; that acknowledged divergence is the documented model. The test
# verifies the stored values read back correctly.
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x


class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y


def store_opt(xs: list[A | None], p: A | None) -> None:
    xs[0] = p


def store_union(xs: list[A | B], p: A | B) -> None:
    xs[0] = p


def append_union(xs: list[A | B], p: A | B) -> None:
    xs.append(p)


def store_dict(d: dict[str, A | None], p: A | None) -> None:
    d["k"] = p           # dict subscript-assign shares the element-store path


def store_narrowed(xs: list[A | B], p: A | B) -> None:
    if isinstance(p, A):
        xs[0] = p        # p is narrowed to a concrete A, not a variant -- the
                         # value-variant lift must be skipped (no double-wrap)


def main() -> None:
    opt: list[A | None] = [A(1), None]
    store_opt(opt, A(5))
    head = opt[0]
    print(head.x if head is not None else -1)   # 5

    u: list[A | B] = [A(1), B(2)]
    store_union(u, B(9))
    e0 = u[0]
    if isinstance(e0, B):
        print(e0.y)                              # 9
    append_union(u, A(7))
    e2 = u[2]
    if isinstance(e2, A):
        print(e2.x)                              # 7

    d: dict[str, A | None] = {}
    store_dict(d, A(11))
    dv = d["k"]
    print(dv.x if dv is not None else -1)        # 11

    nx: list[A | B] = [B(0), B(0)]
    store_narrowed(nx, A(13))
    e3 = nx[0]
    if isinstance(e3, A):
        print(e3.x)                              # 13


main()
