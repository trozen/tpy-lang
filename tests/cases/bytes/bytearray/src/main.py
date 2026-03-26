# bytearray: mutable bytes operations
def main() -> None:
    ba = bytearray(b"\x01\x02\x03")
    print(ba)
    print(len(ba))

    ba.append(4)
    print(ba)

    ba[0] = 10
    print(ba)

    popped = ba.pop()
    print(popped)
    print(ba)

    ba.insert(1, 20)
    print(ba)

    ba.clear()
    print(ba)
    print(len(ba))

main()
