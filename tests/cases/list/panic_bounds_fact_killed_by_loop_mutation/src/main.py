# A bounds fact (i < len(xs)) proven before a loop must not elide the
# subscript check inside it when the body shrinks the container: the
# back-edge re-enters with the fact stale (runtime bounds check).
def main():
    xs = [10, 20, 30]
    i = 2
    if i < len(xs):
        j = 0
        while j < 3:
            print(xs[i])
            xs.pop()
            j += 1


main()
