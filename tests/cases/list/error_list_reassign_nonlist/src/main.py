# Reassigning an inferred list local to a non-list RHS is rejected at sema
# (the element-compat check's "no list element" branch).
def main() -> None:
    xs = [1, 2]
    xs = 5  # tpyc: error(/expected list\[Int32\], got IntLiteral\(5\)/)
    print(xs)

main()
