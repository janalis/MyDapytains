import os
import json
import re
from typing import Any, Dict, Optional
from lxml import etree
from dapitains.metadata.classes import DublinCore, Extension

from utils import get_namespace  # <-- changer core par utils

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAPPING_PATH = os.path.join(BASE_DIR, "metadata_mapping.json")

with open(MAPPING_PATH, encoding="utf-8") as f:
    mapping_config = json.load(f)

def get_multilang_values(
    tree: etree._ElementTree,
    xpath_exprs: Any,
    namespaces: Dict[str, str],
    lang: Optional[str] = None
) -> Dict[Optional[str], str]:
    if isinstance(xpath_exprs, str):
        xpath_exprs = [xpath_exprs]
    values_by_lang: Dict[Optional[str], str] = {}

    xml_ns = get_namespace("xml")
    lang_attr_name = f"{{{xml_ns}}}lang"

    for expr in xpath_exprs:
        if "$lang" in expr:
            if not lang:
                continue  # can't evaluate without a language
            results = tree.xpath(expr, namespaces=namespaces, lang=lang)
            for el in results:
                if isinstance(el, etree._Element) and el.text:
                    values_by_lang[lang] = el.text.strip()
        else:
            results = tree.xpath(expr, namespaces=namespaces)
            for el in results:
                if isinstance(el, etree._Element):
                    lang_attr = el.get(lang_attr_name)
                    if el.text:
                        values_by_lang[lang_attr] = el.text.strip()
    return values_by_lang

def substitute_keywords_xpath(xpath_expr: str, keywords_xpath: str) -> str:
    if "$keywords_xpath" in xpath_expr:
        return xpath_expr.replace("$keywords_xpath", keywords_xpath)
    return xpath_expr

def extract_metadata(filepath: str) -> Dict[str, Any]:
    tree = etree.parse(filepath)
    root = tree.getroot()
    filename = os.path.basename(filepath)

    namespaces = mapping_config.get("default", {}).get("namespaces", {})
    base_properties = mapping_config.get("default", {}).get("properties", {})
    overrides = mapping_config.get("overrides", {})
    keywords_xpath = mapping_config.get("default", {}).get("keywords_xpath")

    xml_ns = get_namespace("xml")
    lang_attr_name = f"{{{xml_ns}}}lang"

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

    # Extraction des langues disponibles dans les <keywords xml:lang="...">
    langs = {
        kw.get(lang_attr_name)
        for kw in root.xpath(keywords_xpath, namespaces=namespaces)
        if kw.get(lang_attr_name)
    }

    for term, xpath_expr in properties.items():
        combined_lang_values: Dict[Optional[str], str] = {}
        if "$lang" in xpath_expr:
            for lang in langs:
                lang_values = get_multilang_values(tree, xpath_expr, namespaces, lang=lang)
                combined_lang_values.update(lang_values)
        else:
            combined_lang_values = get_multilang_values(tree, xpath_expr, namespaces)

        if not combined_lang_values:
            continue

        short_term = term.split(":", 1)[1] if ":" in term else term
        metadata[short_term] = combined_lang_values

        target = "dublin_core" if term.startswith("dc:") else "extensions"
        for lang, value in combined_lang_values.items():
            if target == "dublin_core":
                metadata["dublin_core"].append(DublinCore(term=short_term, value=value, language=lang))
            else:
                metadata["extensions"].append(Extension(term=short_term, value=value, language=lang))

    multi_lang_ext = mapping_config.get("default", {}).get("multi_lang_extensions", {})
    for term, xpath_expr in multi_lang_ext.items():
        # remplacer $keywords_xpath par la valeur de keywords_xpath de la config
        xpath_expr = substitute_keywords_xpath(xpath_expr, keywords_xpath)

        values_by_lang: Dict[Optional[str], str] = {}
        for lang in langs:
            result = get_multilang_values(tree, xpath_expr, namespaces, lang=lang)
            values_by_lang.update(result)
        if values_by_lang:
            short_term = term.split(":", 1)[1] if ":" in term else term
            metadata[short_term] = values_by_lang
            for lang, value in values_by_lang.items():
                metadata["extensions"].append(Extension(term=short_term, value=value, language=lang))

    return metadata

def format_multilang_dict(d: Dict[Optional[str], str]) -> list:
    result = []
    for lang, val in d.items():
        if not lang:
            result.append(val)
        else:
            result.append({"lang": lang, "value": val})
    return result


if __name__ == "__main__":
    import pprint

    test_file = os.path.join(
        BASE_DIR, r"C:\Users\augus\Desktop\Stage\stage\MyDapytains\tests\tei\WORK_IS-ST_Sermo01.xml"
    )

    metadata = extract_metadata(test_file)
    pprint.pprint(metadata, width=120, sort_dicts=False)

    if "abstract" in metadata:
        print("Abstract JSON format:")
        print(format_multilang_dict(metadata["abstract"]))
