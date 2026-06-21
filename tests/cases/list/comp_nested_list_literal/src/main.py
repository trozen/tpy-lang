# A list comprehension whose element is itself a list literal: the inner
# list-literal's pending element type must be finalized so the outer
# container's C++ element type resolves (was an internal-error crash).
def main() -> None:
    rows = [[i, i + 1] for i in range(3)]  # tpyc: ok
    print(rows)
    # Mutate through the nested subscript to force a real mutable container
    # (reference-type distinction): a silent copy would not be observed here.
    rows[0][0] = 99
    print(rows)
    print(rows[1][1])

main()
