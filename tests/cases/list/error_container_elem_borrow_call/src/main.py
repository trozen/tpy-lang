# The inverse of `container_elem_from_call`: a BORROW-returning callee (a bare
# `-> list[str]`, no `Own`) at a comprehension element slot. The result aliases
# caller-durable storage, so the owning element slot would copy it silently --
# sema warns and the element keeps rejecting, exactly as the
# `out.append(borrow_rows(i))` spelling of the same store does.
from tpy import int32

SHARED: list[str] = ["s"]


def borrow_rows(n: int32) -> list[str]:
    return SHARED


def main() -> None:
    rs = [borrow_rows(i) for i in [1, 2]]  # tpyc: warning(/copies list/) error(/not yet supported/)
    print(len(rs))


main()
