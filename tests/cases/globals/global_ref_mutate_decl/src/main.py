# `global` of a bare reference-type module global from a method: the method
# mutates the shared list (not a copy), and the mutation is visible at module
# level afterward -- forcing the value-vs-reference distinction.
log = [1]


class Writer:
    def add(self, v: int):
        global log
        log.append(v)


def main():
    Writer().add(2)
    Writer().add(3)
    print(len(log), log[0], log[2])


main()
