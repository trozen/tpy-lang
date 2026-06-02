# Non-generic recursive-union wrapper reference return: a bare `-> Expr` return
# lowers to `Expr&` (reference-type convention); a const param is returned as
# readonly[Expr], and a fresh value needs Own[Expr]. Exec-covers the non-generic
# Expr& return path (the generic path is covered by generic_recursive_record_field).
from tpy import Own, readonly

type Expr = int | list[Expr]

g: Expr = [1, [2, 3], 4]


def leaf_count(e: readonly[Expr]) -> int:
    match e:
        case list() as branches:
            total = 0
            for child in branches:
                total += leaf_count(child)
            return total
        case _:
            return 1


def get_global() -> Expr:
    return g


def first_view(e: Expr) -> readonly[Expr]:
    return e


def build() -> Own[Expr]:
    return [5, 6]


def main() -> None:
    print(leaf_count(get_global()))
    seed: Expr = [7, [8, 9]]
    print(leaf_count(first_view(seed)))
    print(leaf_count(build()))


main()
