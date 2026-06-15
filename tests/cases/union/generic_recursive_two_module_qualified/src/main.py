# Two different modules each export a generic recursive `Tree[T]`, both used
# qualified in one file. The purest exercise of qname-keyed wrapper rendering:
# `treea.Tree` and `treeb.Tree` are distinct C++ types
# (`::tpyapp::treea::Tree` vs `::tpyapp::treeb::Tree`) registered in one
# compilation under the same short name, kept apart by canonical qname.
# Read-only traversal is intentional (targets distinct resolution + rendering).
import treea
import treeb


def main() -> None:
    a: treea.Tree[int] = [1, [2, 3]]
    b: treeb.Tree[int] = [4, [5, [6, 7]]]
    print(treea.leaf_count(a))
    print(treeb.leaf_count(b))


main()
