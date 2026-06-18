# os filesystem layer (getcwd/chdir/listdir/getenv); chdir to /tmp + reducing
# results to machine-independent values keeps it CPython byte-parity-stable.
import os


def main():
    os.chdir("/tmp")
    print(os.getcwd())

    for i in ["a", "b", "c"]:
        with open("/tmp/tpy_os_fs_" + i + ".txt", "w") as f:
            f.write(i)
    names = sorted([n for n in os.listdir("/tmp") if n.startswith("tpy_os_fs_")])
    print(",".join(names))

    # Unset var: None / default. PATH is set in any environment that can run
    # the toolchain, so `is not None` exercises the value-returning branch
    # without printing a machine-specific value.
    print(os.getenv("TPY_DEFINITELY_UNSET_VAR") is None)
    print(os.getenv("TPY_DEFINITELY_UNSET_VAR", "dflt"))
    print(os.getenv("PATH") is not None)


main()
