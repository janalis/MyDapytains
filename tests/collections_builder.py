import os
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET
from dapitains.metadata.classes import DublinCore, Extension

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
DC_NS = "http://purl.org/dc/terms/"
EXP_NS = "exp.com"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MAPPING_PATH = os.path.join(BASE_DIR, "metadata_mapping.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)
with open(MAPPING_PATH, encoding="utf-8") as f:
    mapping = json.load(f)

tei_dir = os.path.join(BASE_DIR, config.get("tei_dir", "tei"))
catalog_dir = os.path.join(BASE_DIR, config.get("catalog_dir", "catalog"))

def strip_accents(text: str) -> str:
    text = unicodedata.normalize('NFD', text)
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn')

def clean_id(text: str) -> str:
    # Nettoie le texte (minuscule, remplace caractères non autorisés par _)
    return re.sub(r"[^\w\-]", "_", text.strip().lower())

def clean_id_with_strip(text: str) -> str:
    text_no_accents = strip_accents(text)      # 1. On enlève les accents
    cleaned = clean_id(text_no_accents)        # 2. On nettoie la chaîne
    return cleaned

def get_xpath_text(tree: ET.ElementTree, xpath: str) -> Optional[str]:
    el = tree.find(xpath, TEI_NS)
    return el.text.strip() if el is not None and el.text else None

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

def build_resource_element(res: Dict[str, Any], relpath: str) -> ET.Element:
    res_el = ET.Element("resource", {
        "identifier": res["identifier"],
        "filepath": os.path.normpath(relpath)
    })

    # Ajout obligatoire du <title>
    ET.SubElement(res_el, "title").text = res.get("title", "Titre inconnu")

    # Autres champs optionnels
    for key in ["description", "creator", "work"]:
        if key in res:
            tag = "author" if key == "creator" else key
            ET.SubElement(res_el, tag).text = res[key]

    dc_el = ET.SubElement(res_el, "dublinCore")
    for dc in res["dublin_core"]:
        el = ET.SubElement(dc_el, dc.term)
        el.set("xmlns", DC_NS)
        el.text = dc.value

    ext_el = ET.SubElement(res_el, "extensions")
    for ext in res["extensions"]:
        tag = ext.term.split("/")[-1] if ext.term != "serie" else "serie"
        ns = DC_NS if ext.term.startswith("http://purl.org/dc/terms/") else EXP_NS
        el = ET.SubElement(ext_el, tag)
        el.set("xmlns", ns)
        el.text = ext.value

    return res_el

def build_collection_element(identifier: str, title: str, description: Optional[str] = None, is_reference: bool = False, filepath: Optional[str] = None) -> ET.Element:
    if is_reference and filepath:
        return ET.Element("collection", {"filepath": filepath})
    col_el = ET.Element("collection", {"identifier": identifier})
    ET.SubElement(col_el, "title").text = title
    if description:
        dc_el = ET.SubElement(col_el, "dublinCore")
        desc_el = ET.SubElement(dc_el, "description")
        desc_el.set("xmlns", DC_NS)
        desc_el.text = description
    return col_el

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def write_index_file(path: str, identifier: str, title: str, description: Optional[str], member_elements: List[ET.Element]):
    ensure_dir(path)
    col_el = build_collection_element(identifier, title, description)
    members_el = ET.SubElement(col_el, "members")
    for m in member_elements:
        members_el.append(m)
    tree = ET.ElementTree(col_el)
    tree.write(os.path.join(path, "index.xml"), encoding="utf-8", xml_declaration=True)

def main():
    ensure_dir(catalog_dir)
    resources = []
    for fn in sorted(os.listdir(tei_dir)):
        if fn.startswith("WORK_") and fn.endswith(".xml"):
            resources.append(extract_metadata(os.path.join(tei_dir, fn)))

    corpora = defaultdict(list)
    for res in resources:
        corpora[clean_id_with_strip(res.get("corpus", "unknown"))].append(res)

    root_members = []
    for corpus_key, corpus_items in corpora.items():
        corpus_name = corpus_items[0].get("corpus", corpus_key)
        corpus_path = os.path.join(catalog_dir, "corpus", corpus_key)

        ags = defaultdict(list)
        for r in corpus_items:
            ags[clean_id_with_strip(r.get("authorgroup", "unknown"))].append(r)

        ag_members = []
        for ag_key, ag_items in ags.items():
            ag_path = os.path.join(corpus_path, "authorgroup", ag_key)

            authors = defaultdict(list)
            for r in ag_items:
                authors[clean_id_with_strip(r.get("creator", "unknown"))].append(r)

            au_members = []
            for au_key, au_items in authors.items():
                au_path = os.path.join(ag_path, "author", au_key)

                wgs = defaultdict(list)
                for r in au_items:
                    wgs[clean_id_with_strip(r.get("workgroup", "unknown"))].append(r)

                wg_members = []
                for wg_key, wg_items in wgs.items():
                    wg_path = os.path.join(au_path, "workgroup", wg_key)

                    members = []
                    for res in wg_items:
                        tei_file_path = os.path.abspath(res["filepath"])
                        tei_rel_path = os.path.relpath(tei_file_path, start=wg_path).replace(os.sep, "/")
                        members.append(build_resource_element(res, tei_rel_path))

                    write_index_file(wg_path, wg_key, f"Regroupement d'œuvres : {wg_key}", None, members)
                    wg_members.append(build_collection_element(
                        identifier=f"https://corpus/{corpus_key}/{ag_key}/{au_key}/{wg_key}",
                        title=f"Regroupement d'œuvres : {wg_key}",
                        is_reference=True,
                        filepath=f"workgroup/{wg_key}/index.xml"
                    ))

                write_index_file(au_path, au_key, f"Auteur : {au_key}", None, wg_members)
                au_members.append(build_collection_element(
                    identifier=f"https://corpus/{corpus_key}/{ag_key}/{au_key}",
                    title=f"Auteur : {au_key}",
                    is_reference=True,
                    filepath=f"author/{au_key}/index.xml"
                ))

            write_index_file(ag_path, ag_key, f"Groupe d'auteurs : {ag_key}", None, au_members)
            ag_members.append(build_collection_element(
                identifier=f"https://corpus/{corpus_key}/{ag_key}",
                title=f"Groupe d'auteurs : {ag_key}",
                is_reference=True,
                filepath=f"authorgroup/{ag_key}/index.xml"
            ))

        write_index_file(corpus_path, corpus_key, f"Corpus : {corpus_name}", None, ag_members)
        root_members.append(build_collection_element(
            identifier=f"https://corpus/{corpus_key}",
            title=f"Corpus : {corpus_name}",
            is_reference=True,
            filepath=f"corpus/{corpus_key}/index.xml"
        ))

    write_index_file(catalog_dir, "https://corpus", "Catalogue des collections", None, root_members)

if __name__ == "__main__":
    main()
