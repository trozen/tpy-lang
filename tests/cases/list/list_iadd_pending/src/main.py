# `+=` on an UNANNOTATED list literal is a mutation: the pending literal must
# resolve to list, not the never-mutated Array form (all target shapes).


def test_direct():
    xs = [1, 2]
    xs += [3]
    xs += [4, 5]
    print(xs)


def test_alias():
    xs = [1, 2]
    ys = xs
    ys += [3]
    ys.append(6)
    print(ys)
    print(xs)


def test_in_branch(flag: bool):
    xs = [1, 2]
    if flag:
        xs += [9]
    print(xs)


def test_name_rhs():
    xs = [1, 2]
    more = [7, 8]
    xs += more
    print(xs, more)


def test_set_aug():
    s = {1, 2}
    s |= {3}
    print(sorted(s))


def main():
    test_direct()
    test_alias()
    test_in_branch(True)
    test_in_branch(False)
    test_name_rhs()
    test_set_aug()


main()
