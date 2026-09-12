# Inverse guard for the jagged-demotion fix: a fully-uniform deep-nested list
# literal must stay a fixed Array at every level (resolved C++ type in the
# snapshot is array<array<array<int,2>,2>,2>). The fix must not over-trigger and
# demote a uniform structure to list.
def main() -> None:
    # type() pins all-Array explicitly: a snapshot alone is parity-blind to
    # Array-vs-vector (both print identically), which once hid an over-demotion.
    xs = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]  # tpyc: type(/Array\[Array\[Array\[int32, 2\], 2\], 2\]/)
    print(xs)

main()
