# A loop-body-first local bound in only ONE arm of an `if` is maybe-unassigned
# after it: the read is rejected rather than compiled into an uninitialized
# slot. "Definite assignment follows the block, not the loop" in
# LANGUAGE_FEATURES; the fix is to assign the name once before the `if`.


def one_arm(flag: bool) -> None:
    if flag:
        for i in range(2):
            v = i + 1
    print("one_arm", v)  # tpyc: error(/may not be assigned at this point/)


one_arm(False)
