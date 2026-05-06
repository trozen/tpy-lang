# Out-of-range indexing throws IndexError -- catchable in user code.
# Exercises list __getitem__, __setitem__, __delitem__, list.pop() empty,
# and bytearray pop. CPython uses a distinct "list assignment index out of
# range" message for write-side bounds violations; matched here.


def main() -> None:
    xs: list[int] = [10, 20, 30]
    try:
        print(xs[5])
    except IndexError as e:
        print("caught:", str(e))

    try:
        xs[5] = 99
    except IndexError as e:
        print("caught:", str(e))

    try:
        del xs[5]
    except IndexError as e:
        print("caught:", str(e))

    empty: list[int] = []
    try:
        empty.pop()
    except IndexError as e:
        print("caught:", str(e))

    ba: bytearray = bytearray(b"")
    try:
        ba.pop()
    except IndexError as e:
        print("caught:", str(e))

    ba2: bytearray = bytearray(b"abc")
    try:
        ba2.pop(99)
    except IndexError as e:
        print("caught:", str(e))

    # Negative-out-of-range path also throws.
    try:
        print(xs[-99])
    except IndexError as e:
        print("caught:", str(e))


main()
