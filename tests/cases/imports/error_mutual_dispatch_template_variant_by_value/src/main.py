# A cycle member's `@dispatch` variant with a template head is defined in the
# header, so it may not take a peer's value type by value.
import a

print(a.run())
