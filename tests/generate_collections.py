import os
import json
import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import List, Dict, Any, Optional
from extract_metadata import extract_metadata

# === CONFIG & MAPPING LOAD ===

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)

TEI_DIR = os.path.join(BASE_DIR, config["tei_directory"])
CATALOG_DIR = os.path.join(BASE_DIR, config["output_directory"])
MAPPING_PATH = os.path.join(BASE_DIR, config["xpath_config_file"])
STATE_PATH = os.path.join(BASE_DIR, config["build_state_file"])

with open(MAPPING_PATH, encoding="utf-8") as f:
    mapping_config = json.load(f)

default_namespaces = mapping_config["default"].get("namespaces", {})

namespaces = dict(default_namespaces)

REQUIRED_NAMESPACES = ["tei", "dc", "ex", "foaf"]

def get_namespace(ns_key: str) -> str:
    if ns_key not in namespaces:
        raise RuntimeError(f"Namespace '{ns_key}' must be defined in the config file namespaces.")
    return namespaces[ns_key]

for ns in REQUIRED_NAMESPACES:
    get_namespace(ns)

# === UTILS ===

def strip_accents(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

def clean_id(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text.strip().lower())

def clean_id_with_strip(text: str) -> str:
    return clean_id(strip_accents(text))

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def unique_filename(base_name: str, existing_names: set) -> str:
    candidate = base_name
    i = 2
    while candidate in existing_names:
        candidate = f"{base_name}_{i}"
        i += 1
    existing_names.add(candidate)
    return candidate

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
        "filepath": os.path.normpath(relpath).replace(os.sep, "/")
    })

    ET.SubElement(res_el, "title").text = res.get("title", "Titre inconnu")

    for key in ["description", "creator", "work"]:
        if key in res:
            tag = "author" if key == "creator" else key
            ET.SubElement(res_el, tag).text = res[key]

    dc_el = ET.SubElement(res_el, "dublinCore")
    dc_el.set("xmlns", get_namespace("dc"))
    for dc in res.get("dublin_core", []):
        el = ET.SubElement(dc_el, dc.term)
        el.text = dc.value

    ext_el = ET.SubElement(res_el, "extensions")
    ext_el.set("xmlns", get_namespace("ex"))
    for ext in res.get("extensions", []):
        tag = ext.term.split("/")[-1] if ext.term != "serie" else "serie"
        el = ET.SubElement(ext_el, tag)
        el.text = ext.value

    return res_el

def build_collection_element(identifier: str, title: str, description: Optional[str] = None, is_reference: bool = False, filepath: Optional[str] = None) -> ET.Element:
    if is_reference and filepath:
        return ET.Element("collection", {"filepath": filepath.replace(os.sep, "/")})
    col_el = ET.Element("collection", {"identifier": identifier})
    ET.SubElement(col_el, "title").text = title
    if description:
        dc_el = ET.SubElement(col_el, "dublinCore")
        desc_el = ET.SubElement(dc_el, "description")
        desc_el.set("xmlns", get_namespace("dc"))
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
    ensure_dir(CATALOG_DIR)

    current_hash = compute_config_hash(CONFIG_PATH, MAPPING_PATH)
    state = load_state()
    previous_hash = state.get("config_hash")
    previous_files = state.get("files", {})

    process_all = current_hash != previous_hash
    current_files = {}
    resources = []

    for fn in sorted(os.listdir(TEI_DIR)):
        if fn.startswith("WORK_") and fn.endswith(".xml"):
            path = os.path.join(TEI_DIR, fn)
            mtime = os.path.getmtime(path)
            rel_path = os.path.relpath(path, BASE_DIR).replace(os.sep, "/")
            current_files[rel_path] = {"mtime": mtime}

            if process_all or rel_path not in previous_files or previous_files[rel_path]["mtime"] != mtime:
                res = extract_metadata(path)
                res["filepath"] = rel_path
                resources.append(res)

    corpora = defaultdict(list)
    for res in resources:
        corpora[clean_id_with_strip(res.get("corpus", "unknown"))].append(res)

    root_members = []
    for corpus_key, corpus_items in corpora.items():
        corpus_name = corpus_items[0].get("corpus", corpus_key)
        corpus_path = os.path.join(CATALOG_DIR, "corpus", corpus_key)
        corpus_identifier = f"{corpus_key}"

        ags = defaultdict(list)
        for r in corpus_items:
            ags[clean_id_with_strip(r.get("authorgroup", "unknown"))].append(r)

        ag_members = []
        for ag_key, ag_items in ags.items():
            ag_name = ag_items[0].get("authorgroup", ag_key)
            ag_path = os.path.join(corpus_path, "authorgroup", ag_key)
            ag_identifier = f"{corpus_key}_{ag_key}"

            authors = defaultdict(list)
            for r in ag_items:
                authors[clean_id_with_strip(r.get("creator", "unknown"))].append(r)

            au_members = []
            for au_key, au_items in authors.items():
                author_name = au_items[0].get("creator", au_key)
                au_path = os.path.join(ag_path, "author", au_key)
                au_identifier = f"{corpus_key}_{ag_key}_{au_key}"

                wgs = defaultdict(list)
                for r in au_items:
                    wgs[clean_id_with_strip(r.get("workgroup", "unknown"))].append(r)

                wg_members = []
                for wg_key, wg_items in wgs.items():
                    wg_name = wg_items[0].get("workgroup", wg_key)
                    wg_path = os.path.join(au_path, "workgroup", wg_key)
                    work_path = os.path.join(wg_path, "work")
                    ensure_dir(work_path)

                    wg_identifier = f"{corpus_key}_{ag_key}_{au_key}_{wg_key}"
                    existing_names = set()
                    resource_refs = []

                    for i, res in enumerate(wg_items, 1):
                        raw_title = res.get("workTitle") or res.get("title") or f"sermon_{i}"
                        base_name = clean_id_with_strip(raw_title)
                        filename = unique_filename(base_name, existing_names) + ".xml"
                        filepath = os.path.join(work_path, filename)

                        tei_abs_path = os.path.abspath(os.path.join(BASE_DIR, res["filepath"]))
                        rel_path_to_tei = os.path.relpath(tei_abs_path, start=work_path).replace(os.sep, "/")

                        res_el = build_resource_element(res, rel_path_to_tei)
                        tree = ET.ElementTree(res_el)
                        tree.write(filepath, encoding="utf-8", xml_declaration=True)

                        resource_refs.append(ET.Element("resource", {
                            "filepath": os.path.join("work", filename).replace(os.sep, "/")
                        }))

                    write_index_file(wg_path, wg_identifier, f"Regroupement d'œuvres : {wg_name}", None, resource_refs)

                    wg_members.append(build_collection_element(
                        identifier=wg_identifier,
                        title=f"Regroupement d'œuvres : {wg_name}",
                        is_reference=True,
                        filepath=os.path.relpath(os.path.join(wg_path, "index.xml"), start=au_path).replace(os.sep, "/")
                    ))

                write_index_file(au_path, au_identifier, f"Auteur : {author_name}", None, wg_members)
                au_members.append(build_collection_element(
                    identifier=au_identifier,
                    title=f"Auteur : {author_name}",
                    is_reference=True,
                    filepath=os.path.relpath(os.path.join(au_path, "index.xml"), start=ag_path).replace(os.sep, "/")
                ))

            write_index_file(ag_path, ag_identifier, f"Groupe d'auteurs : {ag_name}", None, au_members)
            ag_members.append(build_collection_element(
                identifier=ag_identifier,
                title=f"Groupe d'auteurs : {ag_name}",
                is_reference=True,
                filepath=os.path.relpath(os.path.join(ag_path, "index.xml"), start=corpus_path).replace(os.sep, "/")
            ))

        write_index_file(corpus_path, corpus_identifier, f"Corpus : {corpus_name}", None, ag_members)
        root_members.append(build_collection_element(
            identifier=corpus_identifier,
            title=f"Corpus : {corpus_name}",
            is_reference=True,
            filepath=os.path.relpath(os.path.join(corpus_path, "index.xml"), start=CATALOG_DIR).replace(os.sep, "/")
        ))

    write_index_file(CATALOG_DIR, "root", "Index général", None, root_members)

    state["config_hash"] = current_hash
    state["files"] = current_files
    save_state(state)

if __name__ == "__main__":
    main()
