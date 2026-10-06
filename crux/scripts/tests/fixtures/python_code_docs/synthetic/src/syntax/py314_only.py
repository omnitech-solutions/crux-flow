"""Valid on Python 3.14 (PEP 758), a SyntaxError on 3.13."""


def g():
    try:
        pass
    except ValueError, TypeError:
        pass
