# A plain function driving range() with non-unit literal steps -- an ascending
# positive step and a descending negative step -- so the stepped-loop bound and
# direction arms are exec-observed, not just byte-diff-checked.
def main() -> None:
    n = 10
    total = 0
    for i in range(0, n, 2):
        total += i
    print(total)

    down = 0
    for j in range(n, 0, -2):
        down += j
    print(down)


main()
