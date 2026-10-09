from xml.etree.ElementTree import Element, ElementTree

from great_agent.errors import XmlValidationError


def require_node(root: Element, xpath: str) -> Element:
    node = root.find(xpath)
    if node is None:
        raise XmlValidationError(f"missing XML node: {xpath}")
    return node


def set_text(root: Element, xpath: str, value: str) -> None:
    require_node(root, xpath).text = f" {value} "


def remove_input(root: Element, name: str) -> None:
    inputs = require_node(root, "./inputs")
    node = inputs.find(name)
    if node is not None:
        inputs.remove(node)


def write_xml(tree: ElementTree, path: str) -> None:
    tree.write(path, encoding="utf-8", xml_declaration=True)
