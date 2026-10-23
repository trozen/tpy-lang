# A generator expression's walrus that binds the enclosing variable cannot
# bind it to a reference to an object, as a list comprehension's cannot: the
# variable outlives the frame, and what the element reaches may not.
class Box:
    def __init__(self, n: int) -> None:
        self.n = n


def main(boxes: list[Box]) -> None:
    last = Box(0)
    # The subject: `last` is read after the generator expression.
    t = sum(1 for b in boxes if (last := b).n > 0)  # tpyc: error(/a walrus inside a comprehension or generator expression cannot bind 'last' to a reference to an object; use a `for` loop/)
    print(t, last.n)


main([Box(1)])
