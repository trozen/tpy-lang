# subprocess.Popen keyword arguments outside the supported set (here shell=)
# are a compile error naming the keyword.
from subprocess import Popen


def main() -> None:
    p = Popen(["ls"], shell=True)  # tpyc: error(/'Popen\(\)' got unexpected keyword argument 'shell'/)
    print(p.wait())


main()
