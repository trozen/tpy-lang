# The unbound-self form only accepts ancestors of the current record. Calling
# an unrelated class's instance method via ClassName.method(self, ...) is
# rejected.


class Ancestor:
    def foo(self) -> int:
        return 1


class Unrelated:
    def bar(self) -> int:
        return 2


class Child(Ancestor):
    def use(self) -> int:
        return Unrelated.bar(self)  # tpyc: error(/not an ancestor of 'Child'/)
