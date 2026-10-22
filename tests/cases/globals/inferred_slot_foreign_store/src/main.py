# A store from another body (`global` / `nonlocal`) into a variable whose
# type its owning scope inferred narrows into that type with a run-time
# check and warns; an annotated first binding or an explicit conversion
# silences it, and a store that does not narrow never warns.
from tpy import uint8, int8, uint32

g = uint8(0)
cnt = 0
n8 = int8(0)
ann: uint8 = 0
conv = uint8(0)


def global_aug() -> None:
    global g
    g += 1  # tpyc: warning(/'g' was inferred as uint8 in its module; this store from 'global_aug' narrows int32/)


def global_plain() -> None:
    global g
    g = g + 1  # tpyc: warning(/'g' was inferred as uint8 in its module/)


def global_walrus() -> None:
    global g
    print("global_walrus:", (g := g + 1))  # tpyc: warning(/'g' was inferred as uint8/)


def global_count() -> None:
    global cnt
    cnt += 1  # tpyc: ok


def global_mixed_sign() -> None:
    global n8
    n8 = uint32(7)  # tpyc: warning(/'n8' was inferred as int8 in its module; this store from 'global_mixed_sign' narrows uint32/)


def global_bigint() -> None:
    global g
    g = int(9)  # tpyc: warning(/'g' was inferred as uint8 in its module; this store from 'global_bigint' narrows int into it/)


def global_annotated() -> None:
    global ann
    ann += 1  # tpyc: ok


def global_explicit() -> None:
    global conv
    conv = uint8(conv + 1)  # tpyc: ok


def nonlocal_stores() -> None:
    k = uint8(0)
    total = 0

    def bump() -> None:
        nonlocal k, total
        k += 1  # tpyc: warning(/'k' was inferred as uint8 in the enclosing function 'nonlocal_stores'/)
        k = k + 1  # tpyc: warning(/'k' was inferred as uint8 in the enclosing function/)
        print("nonlocal_walrus:", (k := k + 1))  # tpyc: warning(/'k' was inferred as uint8/)
        total += 1  # tpyc: ok

    bump()
    print("nonlocal:", k, total)


global_aug()
print("global_aug:", g)
global_plain()
print("global_plain:", g)
global_walrus()
global_count()
print("global_count:", cnt)
global_mixed_sign()
print("global_mixed_sign:", n8)
global_bigint()
print("global_bigint:", g)
global_annotated()
print("global_annotated:", ann)
global_explicit()
print("global_explicit:", conv)
nonlocal_stores()
