import os
import json
import re
from typing import Any, Dict
from lxml import etree
from dapitains.metadata.classes import DublinCore, Extension

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAPPING_PATH = os.path.join(BASE_DIR, "metadata_mapping.json")

with open(MAPPING_PATH, encoding="utf-8") as f:
    mapping_config = json.load(f)

def get_multilang_values(tree: etree._ElementTree, xpath_exprs: Any, namespaces: Dict[str, str]) -> Dict[str, str]:
    if isinstance(xpath_exprs, str):
        xpath_exprs = [xpath_exprs]
    values_by_lang = {}
    for expr in xpath_exprs:
        results = tree.xpath(expr, namespaces=namespaces)
        for el in results:
            if isinstance(el, etree._Element):
                lang = el.get("{http://www.w3.org/XML/1998/namespace}lang", "und")
                if el.text:
                    values_by_lang[lang] = el.text.strip()
    return values_by_lang

def extract_metadata(filepath: str) -> Dict[str, Any]:
    tree = etree.parse(filepath)
    root = tree.getroot()
    filename = os.path.basename(filepath)
    namespaces = {**TEI_NS, **mapping_config.get("default", {}).get("namespaces", {})}
    base_properties = mapping_config.get("default", {}).get("properties", {})
    overrides = mapping_config.get("overrides", {})

    properties = dict(base_properties)
    for pattern, override_properties in overrides.items():
        if pattern == filename or re.match(pattern, filename):
            properties.update(override_properties)
            break

    metadata: Dict[str, Any] = {
        "identifier": os.path.splitext(filename)[0],
        "filepath": filepath,
        "dublin_core": [],
        "extensions": [],
    }

    for term, xpath_expr in properties.items():
        lang_values = get_multilang_values(tree, xpath_expr, namespaces)
        if not lang_values:
            continue
        short_term = term.split(":", 1)[1] if ":" in term else term
        metadata[short_term] = lang_values
        target = "dublin_core" if term.startswith("dc:") else "extensions"
        for lang, value in lang_values.items():
            if target == "dublin_core":
                metadata["dublin_core"].append(DublinCore(term=short_term, value=value, language=lang))
            else:
                metadata["extensions"].append(Extension(term=short_term, value=value, language=lang))

    langs = {
        kw.get("{http://www.w3.org/XML/1998/namespace}lang")
        for kw in root.xpath(".//tei:profileDesc/tei:textClass/tei:keywords", namespaces=namespaces)
        if kw.get("{http://www.w3.org/XML/1998/namespace}lang")
    }

    multi_lang_ext = mapping_config.get("default", {}).get("multi_lang_extensions", {})
    for term, xpath_template in multi_lang_ext.items():
        values_by_lang = {}
        for lang in langs:
            results = root.xpath(xpath_template.format(lang=lang), namespaces=namespaces)
            if results and results[0].text:
                values_by_lang[lang] = results[0].text.strip()
        if values_by_lang:
            short_term = term.split(":", 1)[1] if ":" in term else term
            metadata[short_term] = values_by_lang
            for lang, value in values_by_lang.items():
                metadata["extensions"].append(Extension(term=short_term, value=value, language=lang))

    return metadata

if __name__ == "__main__":
    import pprint

    test_file = os.path.join(
        BASE_DIR, r"C:\Users\augus\Desktop\Stage\stage\MyDapytains\tests\tei\WORK_IS-ST_Sermo01.xml"
    )

    pprint.pprint(extract_metadata(test_file), width=120, sort_dicts=False)
