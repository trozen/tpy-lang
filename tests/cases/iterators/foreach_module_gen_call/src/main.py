# Foreach over a MODULE-QUALIFIED generator factory call (`itersrc.counts(n)`,
# the marker-call "qualified" spelling -- not the from-import bare name): the
# universal __iter__/__next__ loop over the cross-module factory.
from tpy import Int32
import itersrc


def total(n: Int32) -> Int32:
    t = 0
    for x in itersrc.counts(n):
        t = t + x
    return t


def main() -> None:
    for v in itersrc.counts(3):
        print(v)
    print(total(5))


main()
