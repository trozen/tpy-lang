from tpy import int32
import cy


# Only forwards to cy.count, which reads: `xs` is a const reference.
def forward(xs: list[int32]) -> int32:
    return cy.count(xs)


def one() -> int32:
    return 1
