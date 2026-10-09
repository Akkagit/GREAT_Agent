from enum import StrEnum

from great_agent.domain.plans import Scenario


class ProductKind(StrEnum):
    IFCB = "ifcb"
    UPD = "upd"


def required_products(scenario: Scenario) -> frozenset[ProductKind]:
    products: set[ProductKind] = set()
    if scenario.frequency == "TF":
        products.add(ProductKind.IFCB)
    if scenario.ambiguity == "fixed":
        products.add(ProductKind.UPD)
    return frozenset(products)
