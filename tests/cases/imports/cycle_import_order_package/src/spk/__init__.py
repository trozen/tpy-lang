# The package starts its submodule `a`, which runs while `spk` is still being
# imported.
import spk.a


def main() -> None:
    spk.a.main()
