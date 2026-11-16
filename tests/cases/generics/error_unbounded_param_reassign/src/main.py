# An unbounded `T` parameter cannot be reassigned (its parameter form is a
# mutable reference at a reference instantiation); a ValueType-bounded one can
# (BUGS.md#generic-param-reassign-rejected).


def pick[T](x: T, y: T) -> T:
    x = y  # tpyc: error(/Cannot reassign parameter 'x' of type 'T'/)
    return x
