# Loop vars leak past the loop (Python scoping): the value bound in the body
# aliases the LAST element after the loop, and mutating through it reaches
# the container. Same for list-of-tuples unpacks. The directory name still says
# `items`, but the head this pins is a dict LITERAL local -- a `.items()` head
# is a call and proves nothing, which `error_loop_body_local_after_items`
# pins instead.
from tpy import int32


def main():
    # A dict literal is the local's only binding, so iterating it proves the
    # loop runs and the names it binds are readable after it (`d.items()`
    # would not prove -- it is a call).
    d = {"a": [1, 2, 3], "b": [4, 5]}
    for k in d:
        v = d[k]
    print(k, len(v))
    v.append(6)
    print(d["b"])

    pairs: list[tuple[int32, list[int32]]] = [(1, [10]), (2, [20])]
    for n, xs in pairs:
        pass
    xs.append(30)
    print(n, pairs[1][1])


main()
