# Identity recursion requires the declared params in order: swapping them at
# the recursive position (Pair[V, K] instead of Pair[K, V]) is rejected.

# tpyc: error(/recursive position of alias 'Pair' must reuse type parameter 'K' at position 0/)
type Pair[K, V] = tuple[K, V] | dict[V, Pair[V, K]]


def main() -> None:
    print("never reached")


main()
