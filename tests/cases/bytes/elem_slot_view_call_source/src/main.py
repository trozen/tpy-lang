# A VIEW-returning CALL at an `Own[bytes]` element slot: the slot STORES, and
# the runtime does not build the element itself, so the view materializes there
# (`bytes_copy`) exactly as a view NAME or a slice does -- the source's SHAPE is
# not what decides it, its form is. A LOOKUP slot is the other half of that
# rule and keeps the view (list/elem_slot_view_key). The copy is forced by
# the element type, not observed: the view's backing store is a literal, so an
# aliasing render would print the same; the render is the pin.
from tpy import BytesView


def view_of(b: bytes) -> BytesView:
    return b


class Sink:
    chunks: list[bytes]
    owned: list[bytes]

    def __init__(self) -> None:
        self.chunks = []
        self.owned = []

    def add_view(self, b: bytes) -> None:
        # The call's result is a BytesView: materialized into the element.
        self.chunks.append(view_of(b))

    def add_owned(self, b: bytes) -> None:
        # INVERSE: a bytes PARAM read is the view form too, so it takes the
        # same materialize -- what tells the two apart is the FORM, and an
        # owned local is what stays bare (the str twin pins that leg).
        self.owned.append(b)


def main() -> None:
    s = Sink()
    s.add_view(b"ab")
    s.add_owned(b"cd")
    # The element is an independent copy, so the container outlives the view.
    print(len(s.chunks), s.chunks[0], s.owned[0])


main()
