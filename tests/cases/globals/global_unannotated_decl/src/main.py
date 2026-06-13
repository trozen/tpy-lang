# `global` of a bare (unannotated) module global must be accepted and mutate
# the shared global, from a free function, a method, and a staticmethod alike.
count = 0


def bump():
    global count
    count += 1


def reset():
    global count
    count = 0  # plain assign to a bare global, not just augmented


class P:
    def __init__(self):
        global count
        count += 1

    @staticmethod
    def boost():
        global count
        count += 10


def main():
    bump()
    P()
    P.boost()
    print(count)
    reset()
    print(count)


main()
