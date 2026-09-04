# A record field read one Deref hop deep (`body.material.color` off a
# `Box[Material]` field) as a method receiver and as a record ref argument,
# including the same reads through a `Ptr[Body]` binding, and a write through
# the same chain. CPython runs it through the Deref stub's attribute forwarding.
from tpy import Own, Ptr, nocopy
from tplib import Box


# @nocopy: the reads are reference-bound, so a silent copy is an error.
@nocopy
class V3:
    x: float

    def __init__(self, x_: float) -> None:
        self.x = x_

    def mul(self, v: "V3") -> Own["V3"]:
        return V3(self.x * v.x)


class Material:
    color: V3

    def __init__(self, color: Own[V3]) -> None:
        self.color = color

    def bounce(self, k: float) -> float:
        return self.color.x * k


class Body:
    material: Box[Material]

    def __init__(self, material: Own[Box[Material]]) -> None:
        self.material = material


def main() -> None:
    base = V3(3.0)
    b = Body(Box(Material(V3(2.0))))
    # A method call whose receiver reads through the Box.
    print(b.material.bounce(5.0))  # tpyc: ok
    # ... and the same read as a record ref argument.
    print(base.mul(b.material.color).x)  # tpyc: ok
    bodies = [Body(Box(Material(V3(4.0))))]
    hit: Ptr[Body] = None
    for i in range(len(bodies)):
        hit = bodies[i]
    if hit is None:
        return
    # Both shapes again with a Ptr[Body] first hop.
    print(hit.material.bounce(5.0))  # tpyc: ok
    print(base.mul(hit.material.color).x)  # tpyc: ok
    # `hit` names the list's own element, so a write through it is visible
    # on a later read of the list.
    hit.material.color = V3(8.0)
    for body in bodies:
        print(body.material.bounce(1.0))


main()
