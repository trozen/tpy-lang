# Nested containers: list of sets, set in tuple
from tpy import int32

def main() -> None:
    sets: list[set[int32]] = [{1, 2}, {3, 4}]
    print(sets)
    sets[0].add(5)
    print(sets)

    t: tuple[set[int32], str] = ({10, 20}, "hello")
    print(t)

main()
