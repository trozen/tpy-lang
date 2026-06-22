# A dict comprehension whose value is a collection literal: the brace-init
# value is made self-describing (T{...}) so insert_or_assign can deduce it.
def main() -> None:
    # Fixed-size inner list literal -> Array value.
    fixed = {i: [i, i + 1] for i in range(3)}  # tpyc: ok
    print(fixed)

    # Variable-size inner comprehension -> vector value; grow one through the
    # subscript and observe it (reference semantics, not a per-entry copy).
    jagged = {i: [k for k in range(i)] for i in range(4)}  # tpyc: ok
    print(jagged)
    jagged[2].append(99)
    print(jagged)

main()
