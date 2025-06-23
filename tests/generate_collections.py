import os
import json
import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import List, Dict, Any, Optional
from extract_metadata import extract_metadata

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

namespaces = dict(mapping_config["default"].get("namespaces", {}))

# Enregistrer les préfixes pour éviter les ns0, ns1 dans le XML final
for prefix, uri in namespaces.items():
    ET.register_namespace(prefix, uri)

REQUIRED_NAMESPACES = [ns.split(":")[0] for h in config["hierarchy"] for ns in [h["key"]]]
for ns in REQUIRED_NAMESPACES:
    if ns not in namespaces:
        raise RuntimeError(f"Namespace '{ns}' must be defined in the config file namespaces.")

def get_namespace(ns_key: str) -> str:
    return namespaces[ns_key]

def strip_accents(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

def clean_id(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text.strip().lower())

def clean_id_with_strip(text: str) -> str:
    return clean_id(strip_accents(text)) if text else "unknown"

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

def build_resource_element(res: Dict[str, Any], relpath: str) -> ET.Element:
    res_el = ET.Element("resource", {
        "identifier": res["identifier"],
        "filepath": os.path.normpath(relpath).replace(os.sep, "/")
    })

    for lang, val in res.get("title", {"und": "Titre inconnu"}).items():
        title_el = ET.SubElement(res_el, "title")
        title_el.text = val
        title_el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)

    for key, tag in [("description", "description"), ("creator", "author"), ("work", "work")]:
        if key in res:
            for lang, val in res[key].items():
                el = ET.SubElement(res_el, tag)
                el.text = val
                el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)

    dc_el = ET.SubElement(res_el, "dublinCore")
    dc_ns = get_namespace("dc")
    for dc in res.get("dublin_core", []):
        el = ET.SubElement(dc_el, f"{{{dc_ns}}}{dc.term}")
        el.text = dc.value
        if dc.language:
            el.set("{http://www.w3.org/XML/1998/namespace}lang", dc.language)

    ext_el = ET.SubElement(res_el, "extensions")
    ex_ns = get_namespace("ex")
    for ext in res.get("extensions", []):
        tag = ext.term.split("/")[-1] if ext.term != "serie" else "serie"
        el = ET.SubElement(ext_el, f"{{{ex_ns}}}{tag}")
        el.text = ext.value
        if ext.language:
            el.set("{http://www.w3.org/XML/1998/namespace}lang", ext.language)

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

    def recursive_group(level: int, parent_path: str, items: List[Dict[str, Any]], parent_id: str) -> List[ET.Element]:
        if level >= len(config["hierarchy"]):
            return []

        current_config = config["hierarchy"][level]
        key = current_config["key"]
        title_label = current_config["title"]
        slug = current_config["slug"]
        level_key = key.split(":")[-1]
        if_missing = current_config.get("if_missing", "create_unknown")

        groups = defaultdict(list)
        attach_to_parent_items = []

        for item in items:
            value = item.get(level_key)
            if not value:
                if if_missing == "skip":
                    continue
                elif if_missing == "attach_to_parent":
                    attach_to_parent_items.append(item)
                    continue
                elif if_missing == "create_unknown":
                    value = f"Unknown {slug}"
            group_id = clean_id_with_strip(value.get("en") if isinstance(value, dict) else value)
            groups[group_id].append(item)

        members = []
        for group_id, group_items in groups.items():
            first = group_items[0]
            name_data = first.get(level_key)
            group_name = name_data.get("en") if isinstance(name_data, dict) else name_data
            group_identifier = f"{parent_id}_{group_id}" if parent_id else group_id
            group_path = os.path.join(parent_path, slug, group_id) if level < len(config["hierarchy"]) - 1 else os.path.join(parent_path, slug)

            if level == len(config["hierarchy"]) - 1:
                ensure_dir(group_path)
                existing_names = set()
                for res in group_items:
                    raw_title = res.get("workTitle") or res.get("title", {}).get("en") or "work"
                    base_name = clean_id_with_strip(raw_title)
                    filename = unique_filename(base_name, existing_names) + ".xml"
                    filepath = os.path.join(group_path, filename)
                    tei_abs_path = os.path.abspath(os.path.join(BASE_DIR, res["filepath"]))
                    rel_path_to_tei = os.path.relpath(tei_abs_path, start=group_path).replace(os.sep, "/")
                    res_el = build_resource_element(res, rel_path_to_tei)
                    ET.ElementTree(res_el).write(filepath, encoding="utf-8", xml_declaration=True)
                members.append(build_collection_element(
                    identifier=group_identifier,
                    title=f"{title_label} : {group_name}",
                    is_reference=True,
                    filepath=os.path.relpath(filepath, start=parent_path).replace(os.sep, "/")
                ))
            else:
                sub_members = recursive_group(level + 1, group_path, group_items, group_identifier)
                write_index_file(group_path, group_identifier, f"{title_label} : {group_name}", None, sub_members)
                members.append(build_collection_element(
                    identifier=group_identifier,
                    title=f"{title_label} : {group_name}",
                    is_reference=True,
                    filepath=os.path.relpath(os.path.join(group_path, "index.xml"), start=parent_path).replace(os.sep, "/")
                ))

        if attach_to_parent_items:
            members += recursive_group(level + 1, parent_path, attach_to_parent_items, parent_id)

        return members

    root_members = recursive_group(0, CATALOG_DIR, resources, "")
    write_index_file(CATALOG_DIR, "root", "Index général", None, root_members)
    state["config_hash"] = current_hash
    state["files"] = current_files
    save_state(state)

if __name__ == "__main__":
    main()
