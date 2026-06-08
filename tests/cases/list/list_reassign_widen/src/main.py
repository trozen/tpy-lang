# Regression guard: reassigning an inferred list local to a different-size list
# of the same element family stays valid (element-compat must not over-reject).
def main() -> None:
    xs = [1, 2]
    xs = [3, 4, 5]
    print(len(xs))
    xs.append(6)
    print(len(xs), xs[3])

main()
