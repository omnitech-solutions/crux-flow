"""PEP 695 generics."""

type Pair[T] = tuple[T, T]


def first[T](pair: Pair[T]) -> T:
    """Return the first item."""
    return pair[0]


class Box[T]:
    """A box."""

    def get(self) -> T:
        return self.item
