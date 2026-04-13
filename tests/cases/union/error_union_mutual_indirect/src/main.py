# Error: indirect mutual recursion without Box (A -> B -> union -> A)
type Node = Leaf | Branch  # tpyc: error(/infinite-size cycle/)

class Leaf:
    value: int

class Branch:
    child: Node
