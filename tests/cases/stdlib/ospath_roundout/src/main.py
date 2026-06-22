# os.path round-out: normcase, samestat, realpath(strict=). Output is
# host-independent: bools + comparisons, not raw stat/path values.
import os


def main() -> None:
    print(os.path.normcase("/A/b.TXT"))

    tmp = os.path.realpath("/tmp")
    d = tmp + "/tpy_ospath_roundout"
    if os.path.exists(d):
        os.rmdir(d)
    os.mkdir(d)

    # samestat: two stats of the same dir match; a different dir does not.
    s = os.stat(d)
    print("same:", os.path.samestat(s, os.stat(d)))
    print("diff:", os.path.samestat(os.stat(tmp), s))

    # realpath: strict=True on an existing path equals the loose result.
    print("strict-eq:",
          os.path.realpath(d, strict=True) == os.path.realpath(d, strict=False))
    missing = d + "/nope"
    # strict=False never fails on a missing path.
    print("loose-ok:", len(os.path.realpath(missing, strict=False)) > 0)
    # strict=True raises FileNotFoundError on a missing path.
    try:
        os.path.realpath(missing, strict=True)
    except FileNotFoundError:
        print("strict FileNotFoundError")

    # realpath resolves a symlink to its target (the behavior that
    # distinguishes it from abspath); a dangling symlink fails under strict.
    lnk = tmp + "/tpy_ospath_roundout_lnk"
    if os.path.lexists(lnk):
        os.remove(lnk)
    os.symlink(d, lnk)
    print("symlink-resolves:",
          os.path.realpath(lnk, strict=True) == os.path.realpath(d, strict=True))
    os.remove(lnk)

    dangling = tmp + "/tpy_ospath_roundout_dangling"
    if os.path.lexists(dangling):
        os.remove(dangling)
    os.symlink(d + "/gone", dangling)
    try:
        os.path.realpath(dangling, strict=True)
    except FileNotFoundError:
        print("dangling FileNotFoundError")
    os.remove(dangling)

    os.rmdir(d)


main()
