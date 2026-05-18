# Ptr field-path narrowing: skip deref_check after `self.field is not None`
from tpy import Ptr, Int32, readonly, copy

class Node:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value
    def get_value(self) -> Int32:
        return self.value

class Container:
    node: Ptr[Node]
    def __init__(self, node: Ptr[Node]) -> None:
        self.node = node

    def read_if_present(self) -> Int32:
        if self.node is not None:
            return self.node.value  # tpyc: non_null(self.node)
        return Int32(-1)

    def call_if_present(self) -> Int32:
        if self.node is not None:
            return self.node.get_value()  # tpyc: non_null(self.node)
        return Int32(-1)

    def read_without_check(self) -> Int32:
        return self.node.value  # tpyc: nullable(self.node)

    def read_after_merge(self) -> Int32:
        if self.node is not None:
            pass
        return self.node.value  # tpyc: nullable(self.node)

    def mutate(self) -> None:
        pass

    # Method call on self invalidates self.node narrowing
    def read_after_method_call(self) -> Int32:
        if self.node is not None:
            self.mutate()
            return self.node.value  # tpyc: nullable(self.node)
        return Int32(-1)

    # Field reassignment invalidates narrowing
    def read_after_reassign(self, other: Ptr[Node]) -> Int32:
        if self.node is not None:
            self.node = other
            return self.node.value  # tpyc: nullable(self.node)
        return Int32(-1)

class Wrapper:
    inner: Container
    def __init__(self, inner: Container) -> None:
        self.inner = copy(inner)

    # Method call on nested field invalidates its sub-path narrowing
    def read_after_inner_mutate(self) -> Int32:
        if self.inner.node is not None:
            self.inner.mutate()
            return self.inner.node.value  # tpyc: nullable(self.inner.node)
        return Int32(-1)

def get_ptr(n: Ptr[Node]) -> Ptr[Node]:
    return n

# Free function: local.field narrowing
def read_field(c: Container) -> Int32:
    if c.node is not None:
        return c.node.value  # tpyc: non_null(c.node)
    return Int32(-1)

# Assert narrows field
def read_after_assert(c: Container) -> Int32:
    assert c.node is not None
    return c.node.value  # tpyc: non_null(c.node)

# Early return narrows field
def read_after_early_return(c: Container) -> Int32:
    if c.node is None:
        return Int32(-1)
    return c.node.value  # tpyc: non_null(c.node)

# Passing a field value (not the container) preserves narrowing
def read_after_field_pass(c: Container) -> Int32:
    if c.node is not None:
        get_ptr(c.node)
        return c.node.value  # tpyc: non_null(c.node)
    return Int32(-1)

# Passing the container itself invalidates field narrowing
def mutate_container(c: Container) -> None:
    pass

def read_after_container_pass(c: Container) -> Int32:
    if c.node is not None:
        mutate_container(c)
        return c.node.value  # tpyc: nullable(c.node)
    return Int32(-1)

def main() -> None:
    n = Node(Int32(42))
    p: Ptr[Node] = n
    c = Container(p)
    print(c.read_if_present())
    print(c.call_if_present())
    print(c.read_without_check())
    print(c.read_after_merge())
    print(c.read_after_method_call())
    print(c.read_after_reassign(p))
    print(read_field(c))
    print(read_after_assert(c))
    print(read_after_early_return(c))
    print(read_after_field_pass(c))
    print(read_after_container_pass(c))
    w = Wrapper(c)
    print(w.read_after_inner_mutate())

main()
