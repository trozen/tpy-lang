# The adjacent stub-method slot a comprehension still cannot fill: an
# `Own[list[T]]` ELEMENT slot moves its argument in, which is not the inline
# stmt-expr render the container/structural slots take.


def a() -> int:
    d: dict[int, list[int]] = {}
    got = d.setdefault(1, [x for x in range(3)])  # tpyc: error(/method.arg_shape/)
    return len(got)


def main():
    print(a())


main()
