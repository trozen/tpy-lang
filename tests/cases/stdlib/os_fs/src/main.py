# os filesystem layer (getcwd/chdir/listdir/getenv). Output is kept
# host-independent: the run's scratch cwd differs per run, so getcwd is
# compared against a path built from it rather than printed.
import os


def main():
    tmp = os.getcwd() + "/tpy_os_fs_dir"
    os.mkdir(tmp)
    os.chdir(tmp)
    print(os.getcwd() == tmp)

    for i in ["a", "b", "c"]:
        with open(tmp + "/tpy_os_fs_" + i + ".txt", "w") as f:
            f.write(i)
    names = sorted([n for n in os.listdir(tmp) if n.startswith("tpy_os_fs_")])
    print(",".join(names))

    # Unset var: None / default. PATH is set in any environment that can run
    # the toolchain, so `is not None` exercises the value-returning branch
    # without printing a machine-specific value.
    print(os.getenv("TPY_DEFINITELY_UNSET_VAR") is None)
    print(os.getenv("TPY_DEFINITELY_UNSET_VAR", "dflt"))
    print(os.getenv("PATH") is not None)


main()
