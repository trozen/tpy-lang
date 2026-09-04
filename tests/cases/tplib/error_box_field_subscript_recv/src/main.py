# A Deref-wrapper field read rooted at a container SUBSCRIPT rather than a
# name or a Ptr: the element read composes differently from the flat member
# chain the row renders, so it must keep rejecting.
from tpy import Own
from tplib import Box


class Material:
    k: float

    def __init__(self, k: float) -> None:
        self.k = k

    def bounce(self, x: float) -> float:
        return self.k * x


class Body:
    material: Box[Material]

    def __init__(self, material: Own[Box[Material]]) -> None:
        self.material = material


def main() -> None:
    bodies = [Body(Box(Material(2.0)))]
    print(bodies[0].material.bounce(3.0))  # tpyc: error(/method.marker.deref.recv_shape/)


main()
