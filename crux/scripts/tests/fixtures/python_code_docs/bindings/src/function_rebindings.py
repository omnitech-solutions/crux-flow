"""Function-scope rebindings where the nested def stays documented."""


def make_config():
    """Builds nested config helpers."""

    def loader():
        """Original nested loader."""

    loader = "static-loader-value"

    def reader():
        """Original nested reader."""

    import reader as reader

    return loader, reader
