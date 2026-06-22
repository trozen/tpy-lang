# A jagged nested list whose sublists have incompatible element types must be
# rejected as mixed types, not reconciled: [1, 2] (int) and ["a", "b", "c"]
# (str) share no element type, so the different-size reconcile must bail out.
def main() -> None:
    xs = [[1, 2], ["a", "b", "c"]]  # tpyc: error(/mixed types/)
    print(xs)

main()
