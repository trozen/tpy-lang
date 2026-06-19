# An inferred ref-type field on a subclass stores and mutates correctly. The
# field store copies the list (TPy reference-type field semantics, warned), so
# this observes the field's own contents -- not aliasing back to the source.
class Holder:
    pass

class Bag(Holder):
    def __init__(self, items: list[int]):
        self.items = items

def main() -> None:
    xs: list[int] = [1, 2]
    b = Bag(xs)
    b.items.append(3)
    print(len(b.items))
    print(b.items[2])

main()
