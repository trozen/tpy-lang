# Class constants on a record imported from another module.
# Verifies the codegen path uses imported_record_qualification to emit
# `<namespace>::Limits::MAX_RETRIES` rather than a bare local name.
from limits import Limits


def main() -> None:
    print(Limits.MAX_RETRIES)
    print(Limits.GREETING)


main()
