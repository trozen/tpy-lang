# A fixed-count repeat into a stack Array evaluates the element once and
# copies each slot in place -- no buffer default-construction running element
# ctor side effects N extra times. Multi-element repeats interleave. Copy
# semantics per slot are intended (CPython aliases the same object instead --
# the documented repeat divergence), so this test only reads, never mutates
# through an alias.
log: list[int] = []


class P:
    def __init__(self):
        log.append(1)


def main():
    ps = [P()] * 3
    print(len(ps), len(log))
    pair = [10, 20] * 3
    print(len(pair), pair[0], pair[1], pair[3], pair[5])


main()
