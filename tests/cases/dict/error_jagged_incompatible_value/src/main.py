# Dict values that are jagged lists with incompatible element types must be
# rejected, not reconciled: [1, 2] (int) and ["a", "b", "c"] (str) share no
# element type, so the demotion hook must bail out rather than converge them.
def main() -> None:
    d = {1: [1, 2], 2: ["a", "b", "c"]}  # tpyc: error(/mixed value types/)
    print(d)

main()
