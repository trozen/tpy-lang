# A negative user __len__ raises ValueError from both len() and truthiness,
# for a `-> int` (BigInt) and a `-> Int32` __len__; zero/positive keep working.

from tpy import Int32


class NegBig:
    def __len__(self) -> int:
        return -1


class NegI32:
    def __len__(self) -> Int32:
        return -1


class Empty:
    def __len__(self) -> int:
        return 0


class Full:
    def __len__(self) -> Int32:
        return 3


def main() -> None:
    # negative -> ValueError via len(), both return-type branches
    try:
        print(len(NegBig()))
    except ValueError as e:
        print("len big:", e)
    try:
        print(len(NegI32()))
    except ValueError as e:
        print("len i32:", e)

    # negative -> ValueError via truthiness (same slot in CPython), both branches
    try:
        if NegBig():
            print("neg truthy")
    except ValueError as e:
        print("truthy big:", e)
    try:
        if NegI32():
            print("neg truthy")
    except ValueError as e:
        print("truthy i32:", e)

    # inverse -- zero and positive keep working
    print(len(Empty()))
    if Empty():
        print("empty truthy")
    else:
        print("empty falsy")
    print(len(Full()))
    if Full():
        print("full truthy")


main()
