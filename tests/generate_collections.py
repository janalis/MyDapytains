import os
import json
import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import List, Dict, Any, Optional
from extract_metadata import extract_metadata

from dapitains.metadata.classes import DublinCore, Extension

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
DC_NS = "http://purl.org/dc/terms/"
EXP_NS = "exp.com"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MAPPING_PATH = os.path.join(BASE_DIR, "metadata_mapping.json")
STATE_PATH = os.path.join(BASE_DIR, "build_state.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)

tei_dir = os.path.join(BASE_DIR, config.get("tei_dir", "tei"))
catalog_dir = os.path.join(BASE_DIR, config.get("catalog_dir", "catalog"))

# === UTILS ===

def strip_accents(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

def clean_id(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text.strip().lower())

def clean_id_with_strip(text: str) -> str:
    return clean_id(strip_accents(text))

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

# === STATE MANAGEMENT ===

def compute_config_hash(*paths: List[str]) -> str:
    hasher = hashlib.sha256()
    for path in paths:
        with open(path, 'rb') as f:
            hasher.update(f.read())
    return hasher.hexdigest()

def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(data: Dict[str, Any]):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# === XML BUILDING ===

def build_resource_element(res: Dict[str, Any], relpath: str) -> ET.Element:
    res_el = ET.Element("resource", {
        "identifier": res["identifier"],
        "filepath": os.path.normpath(relpath)
    })

    ET.SubElement(res_el, "title").text = res.get("title", "Titre inconnu")

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

def write_index_file(path: str, identifier: str, title: str, description: Optional[str], member_elements: List[ET.Element]):
    ensure_dir(path)
    col_el = build_collection_element(identifier, title, description)
    members_el = ET.SubElement(col_el, "members")
    for m in member_elements:
        members_el.append(m)
    tree = ET.ElementTree(col_el)
    tree.write(os.path.join(path, "index.xml"), encoding="utf-8", xml_declaration=True)

# === MAIN ===

def main():
    ensure_dir(catalog_dir)

    current_hash = compute_config_hash(CONFIG_PATH, MAPPING_PATH)
    state = load_state()
    previous_hash = state.get("config_hash")
    previous_files = state.get("files", {})

    process_all = current_hash != previous_hash
    current_files = {}
    resources = []

    for fn in sorted(os.listdir(tei_dir)):
        if fn.startswith("WORK_") and fn.endswith(".xml"):
            path = os.path.join(tei_dir, fn)
            mtime = os.path.getmtime(path)
            rel_path = os.path.relpath(path, BASE_DIR).replace(os.sep, "/")
            current_files[rel_path] = {"mtime": mtime}

            if process_all or rel_path not in previous_files or previous_files[rel_path]["mtime"] != mtime:
                resources.append(extract_metadata(path))

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

    save_state({
        "config_hash": current_hash,
        "files": current_files
    })

if __name__ == "__main__":
    main()
