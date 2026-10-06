"""Fence edge cases in docstrings."""


def has_both_fences():
    """Docstring with two embedded fence styles.

    ```
    triple backtick fence
    ```

    ~~~
    tilde fence
    ~~~
    """


def has_four_backtick_run():
    """Docstring containing a run of four backticks: ````."""


def has_five_backtick_run():
    """Docstring containing a run of five backticks: `````."""
