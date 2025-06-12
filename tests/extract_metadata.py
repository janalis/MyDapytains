import os
import json
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional
from dapitains.metadata.classes import DublinCore, Extension

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAPPING_PATH = os.path.join(BASE_DIR, "metadata_mapping.json")
CACHE_PATH = os.path.join(BASE_DIR, "build_index.json")

with open(MAPPING_PATH, encoding="utf-8") as f:
    mapping = json.load(f)

def get_xpath_text(tree: ET.ElementTree, xpath: str) -> Optional[str]:
    el = tree.find(xpath, TEI_NS)
    return el.text.strip() if el is not None and el.text else None

def load_cache() -> Dict[str, float]:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(cache: Dict[str, float]):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

def extract_metadata(filepath: str) -> Dict[str, Any]:
    tree = ET.parse(filepath)
    root = tree.getroot()
    metadata = {
        "identifier": os.path.splitext(os.path.basename(filepath))[0],
        "filepath": filepath,
        "dublin_core": [],
        "extensions": [],
    }
    for meta in mapping.get("fields", []):
        value = get_xpath_text(tree, meta["xpath"])
        if not value and meta.get("if_missing"):
            value = meta["if_missing"]
        if meta["target"] == "dc" and value:
            metadata["dublin_core"].append(DublinCore(term=meta["term"], value=value))
            metadata[meta["term"]] = value
        elif meta["target"] == "ext" and value:
            metadata["extensions"].append(Extension(term=meta["term"], value=value))
            metadata[meta["term"]] = value
        elif meta["target"] == "root" and value:
            metadata[meta["term"]] = value

    for item in root.findall(".//tei:fileDesc//tei:sourceDesc//tei:list[@type='metadata']/tei:item", TEI_NS):
        key = item.attrib.get("type", "").strip().lower()
        value = item.text.strip() if item.text else "unknown"
        metadata[key] = value

    return metadata
