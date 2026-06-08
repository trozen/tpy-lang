# Reassigning an inferred str-view local to an incompatible int RHS must be
# rejected at sema, not deferred to the C++ compile step.
def main() -> None:
    s = "asd"
    s = 5  # tpyc: error(/Type mismatch in reassignment to 's': expected str/)
    print(s)

main()
