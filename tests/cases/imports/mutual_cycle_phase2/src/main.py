# An import cycle is analyzed as one unit: facts final only once every
# member's bodies are analyzed (const inference, call-result borrows,
# genexpr call edges, receiver calls into a peer's class) do not depend on
# which member is analyzed first.
from tpy import int32
import ca
import cb
import cy
import cz
import ba
import bb
import ga
import gb
import gy
import gz
import ra
import rb
import ry
import rz


def const_section() -> None:
    xs: list[int32] = [1, 2]
    n = ca.forward(xs)
    m = cz.forward(xs)
    # Same list after the calls: the callees borrowed it, never copied.
    xs.append(3)
    print("const:", n, m, cb.back(xs), cy.back(xs), len(xs))


def borrow_section() -> None:
    print("borrow:", ba.use(), bb.use())


def genexpr_section() -> None:
    rows: list[list[int32]] = [[1], [2, 3]]
    t = ga.total(rows)
    u = gz.total(rows)
    rows[0].append(4)
    print("genexpr:", t, u, gb.back(rows[0]), gy.back(rows[0]), len(rows[0]))


def receiver_section() -> None:
    a = rb.Counter(5)
    y = ry.Counter(6)
    n = ra.peek(a)  # tpyc: ok -- the param is `const rb::Counter&`
    m = rz.peek(y)  # tpyc: ok -- the param is `const ry::Counter&`
    # The callers still own the objects and can write them after the calls.
    a.n += 10
    y.n += 10
    print("receiver:", n, m, ra.peek(a), rz.peek(y))


def method_section() -> None:
    h = ra.Holder(1)
    k = rz.Holder(2)
    t = h.total()  # tpyc: ok -- `total` is a const method
    u = k.total()  # tpyc: ok -- `total` is a const method
    h.xs.append(7)
    k.xs.append(8)
    print("method:", t, u, h.total(), k.total())


def writer_section() -> None:
    xs: list[int32] = [0]
    ra.fill(xs)  # tpyc: ok -- `xs` stays a mutable reference
    rz.fill(xs)  # tpyc: ok -- `xs` stays a mutable reference
    xs.append(9)
    print("writer:", xs)


def main() -> None:
    const_section()
    borrow_section()
    genexpr_section()
    receiver_section()
    method_section()
    writer_section()


main()
