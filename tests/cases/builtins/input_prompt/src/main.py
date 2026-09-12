# input() with and without a prompt: the prompt is written to stdout with no
# trailing newline before the read, and EOF raises EOFError like CPython.
from tpy import String


def main() -> None:
    # prompt form -- the prompt text precedes the answer on the same line
    name = input("s1 name: ")  # tpyc: ok
    print("s1:", name)

    # bare form -- nothing written before the read
    plain = input()  # tpyc: ok
    print("s2:", plain)

    # the prompt may be any str, including a String variable
    where = String("s3 where: ")
    place = input(where)  # tpyc: ok
    print("s3:", place)

    # the fixture is exhausted here, so the read hits EOF
    try:
        input("s4 more: ")  # tpyc: ok
    except EOFError:
        print("s4: eof EOFError")


main()
