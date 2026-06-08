# Reassigning an inferred list local to a list with an incompatible element
# type is rejected at sema (message uses display types, not PendingList repr).
def main() -> None:
    xs = [1, 2]
    xs = ["x"]  # tpyc: error(/expected list\[Int32\], got list\[str\]/)
    print(xs)

main()
