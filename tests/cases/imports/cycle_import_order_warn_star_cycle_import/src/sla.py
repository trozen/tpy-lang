# The import binding `date` shares the cycle import's line but loads no
# module of the cycle: binding `date` by a statement above it works.
import slb; from datetime import date  # noqa: E702,F401
