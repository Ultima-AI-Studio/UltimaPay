"""Explicit compatibility values for legacy data and API aliases.

This is the only new-code compatibility layer allowed to carry the retired
product token. It exists because persisted Gold plan rows still use that
historical product key; changing those rows is outside this split work order.
"""

LEGACY_PRODUCT_REF = "tuppoau"

