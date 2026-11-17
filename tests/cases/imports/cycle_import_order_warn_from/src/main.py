# Import-time from-imports of a cycle peer that is still being imported and
# binds the name only later, or never: TPy warns at each and runs the program.
import wa
import ra
import xa


def main() -> None:
    wa.main()
    ra.main()
    xa.main()


main()
