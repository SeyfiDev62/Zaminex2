"""Pure classification for the attribute ``category`` field.

Kept free of any Django model import so the data migration and the unit tests
can both call the exact same function — the rule is defined once, in one place.
"""

ESSENTIAL = "essential"
NON_ESSENTIAL = "non_essential"


def classify_attribute(is_core: bool, active_binding_count: int) -> str:
    """Return the category an attribute should have.

    The rule:

        essential      ⇔  ``is_core`` OR ``active_binding_count >= 1``
        non_essential  ⇔  otherwise

    ``active_binding_count`` is the number of active bindings the attribute
    participates in (``PropertyTypeAttribute`` or ``DealTypeAttribute`` with
    ``is_active=True``).

    The ``is_core`` clause matters: core attributes (متراژ / قیمت / …) are
    essential *by definition* even when they have no binding row — a
    binding-only rule would misclassify them as non-essential.
    """
    if is_core or active_binding_count > 0:
        return ESSENTIAL
    return NON_ESSENTIAL
