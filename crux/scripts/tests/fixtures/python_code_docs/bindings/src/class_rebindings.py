"""Class-scope rebindings of def statements by non-def operations."""


class Widget:
    """A widget with rebound members."""

    def render(self):
        """Original render method."""

    render = "static-render-value"

    def draw(self):
        """Original draw method."""

    import draw as draw
