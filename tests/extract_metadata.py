import os
import json
from typing import Any, Dict, Optional
from lxml import etree
from dapitains.metadata.classes import DublinCore, Extension

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAPPING_PATH = os.path.join(BASE_DIR, "metadata_mapping.json")
CACHE_PATH = os.path.join(BASE_DIR, "build_index.json")

with open(MAPPING_PATH, encoding="utf-8") as f:
    mapping_config = json.load(f)

def get_first_xpath_text(tree: etree._ElementTree, xpath_exprs: Any, namespaces: Dict[str, str]) -> Optional[str]:
    if isinstance(xpath_exprs, str):
        xpath_exprs = [xpath_exprs]
    for xpath in xpath_exprs:
        results = tree.xpath(xpath, namespaces=namespaces)
        for el in results:
            if isinstance(el, etree._Element) and el.text:
                return el.text.strip()
            elif isinstance(el, str):  # cas d'un XPath retournant directement une string
                return el.strip()
    return None

def extract_metadata(filepath: str) -> Dict[str, Any]:
    tree = etree.parse(filepath)
    root = tree.getroot()

    filename = os.path.basename(filepath)
    namespaces = {**TEI_NS, **mapping_config.get("default", {}).get("namespaces", {})}
    properties = mapping_config.get("default", {}).get("properties", {})

    metadata = {
        "identifier": os.path.splitext(filename)[0],
        "filepath": filepath,
        "dublin_core": [],
        "extensions": [],
    }

    for term, xpath_expr in properties.items():
        value = get_first_xpath_text(tree, xpath_expr, namespaces)
        if not value:
            continue

        if term.startswith("dc:"):
            short_term = term.split(":", 1)[1]
            metadata["dublin_core"].append(DublinCore(term=short_term, value=value))
            metadata[short_term] = value
        elif term.startswith("ex:"):
            short_term = term.split(":", 1)[1]
            metadata["extensions"].append(Extension(term=short_term, value=value))
            metadata[short_term] = value
        else:
            short_term = term.split(":", 1)[-1]
            metadata[short_term] = value

    # Métadonnées supplémentaires via <list type="metadata">
    for item in root.xpath(".//tei:fileDesc//tei:sourceDesc//tei:list[@type='metadata']/tei:item", namespaces=namespaces):
        key = item.get("type", "").strip().lower()
        value = item.text.strip() if item.text else "unknown"
        metadata[key] = value

    return metadata
