# Test bounded type parameters - bound validation at instantiation
# Note: Calling protocol methods on T inside the generic is not yet supported
from typing import Sized

class Container[T: Sized]:
    item: T

    def __init__(self, item: T):
        self.item = item

    def get_item(self) -> T:
        return self.item

def main() -> None:
    # list[int] satisfies Sized, so instantiation works
    c = Container[list[int]]([1, 2, 3])
    items = c.get_item()
    # len() works on the concrete type after retrieval
    print(len(items))
