# What may sit around `super().__init__()` without displacing it from the
# first-statement position the rule demands (error_super_not_first): a
# docstring before it, and a nested `def` after it.

class Parent:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


class Child(Parent):
    extra: int

    def __init__(self, value: int, extra: int) -> None:
        """Initialize Child with value and extra."""
        super().__init__(value)
        self.extra = extra


class Nested(Parent):
    extra: int

    def __init__(self, value: int, extra: int) -> None:
        super().__init__(value)

        # the call above stays the first statement, so this is accepted
        def bonus() -> int:  # tpyc: ok
            return 5

        self.extra = extra + bonus()


c = Child(10, 20)
print(c.value)
print(c.extra)

n = Nested(3, 4)
print(n.value)
print(n.extra)
