import re
from pathlib import Path
from xml.sax.saxutils import quoteattr
from xml.etree.ElementTree import Element, ElementTree
from xml.etree import ElementTree as ET

from great_agent.errors import XmlValidationError


def require_node(root: Element, xpath: str) -> Element:
    node = root.find(xpath)
    if node is None:
        raise XmlValidationError(f"missing XML node: {xpath}")
    return node


def set_text(root: Element, xpath: str, value: str) -> None:
    node = require_node(root, xpath)
    node.text = f" {value} "
    # Text after an inline comment lives in child.tail, not node.text.
    # Clear it too, so template paths cannot survive a replacement.
    for child in node:
        child.tail = None


def remove_input(root: Element, name: str) -> None:
    inputs = require_node(root, "./inputs")
    node = inputs.find(name)
    if node is not None:
        inputs.remove(node)


def write_xml(tree: ElementTree, path: str) -> None:
    content = ET.tostring(tree.getroot(), encoding="unicode")
    node = tree.getroot().find("./filter")
    if node is not None and node.attrib:
        opening = "<filter " + " ".join(
            f"{name}={quoteattr(value)}" for name, value in node.attrib.items()
        ) + " />"
        # Match the serialized element, keeping the template's other layout.
        match = re.search(r"(?m)^([ \t]*)" + re.escape(opening), content)
        if match is not None:
            indent = match.group(1)
            attributes = "\n".join(
                f"{indent}    {name}={quoteattr(value)}"
                for name, value in node.attrib.items()
            )
            formatted = f"{indent}<filter\n{attributes}\n{indent}/>"
            content = content[:match.start()] + formatted + content[match.end():]
    Path(path).write_text(
        "<?xml version='1.0' encoding='utf-8'?>\n" + content,
        encoding="utf-8",
    )
