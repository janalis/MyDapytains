import hashlib
import os
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any, Dict, List, Optional, Union
import xml.etree.ElementTree as ET

# Tes classes DublinCore et Extension importées ici
from dapitains.metadata.classes import DublinCore, Extension

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
DC_NS = "http://purl.org/dc/terms/"
EXP_NS = "http://example.com"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MAPPING_PATH = os.path.join(BASE_DIR, "metadata_mapping.json")
CACHE_PATH = os.path.join(BASE_DIR, "build_index.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)
with open(MAPPING_PATH, encoding="utf-8") as f:
    mapping = json.load(f)

tei_dir = os.path.join(BASE_DIR, config.get("tei_dir", "tei"))
catalog_dir = os.path.join(BASE_DIR, config.get("catalog_dir", "catalog"))

# === UTILS ===

def strip_accents(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

def clean_id(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text.strip().lower())

def clean_id_with_strip(text: str) -> str:
    return clean_id(strip_accents(text))

def get_xpath_text(tree: ET.ElementTree, xpath: str, namespaces=TEI_NS) -> Optional[str]:
    el = tree.find(xpath, namespaces)
    return el.text.strip() if el is not None and el.text else None

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

# === CACHE ===

def load_cache() -> Dict[str, float]:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(cache: Dict[str, float]):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

# === METADATA EXTRACTION ===

def get_mapping_for_file(filename: str) -> Dict[str, Union[Dict[str, str], List[str]]]:
    """Retourne la config de mapping applicable au fichier, avec overrides."""
    # Dictionnaire par défaut
    default = mapping.get("default", {})
    props = default.get("properties", {})
    namespaces = default.get("namespaces", {})

    # Vérifie overrides regex ou nom précis
    overrides = mapping.get("overrides", {})
    for key, override_props in overrides.items():
        try:
            if key.startswith("^") and key.endswith("$"):  # regex
                if re.match(key, filename):
                    props = {**props, **override_props}
            else:  # nom exact
                if key == filename:
                    props = {**props, **override_props}
        except re.error:
            # Pas une regex valide, ignore
            pass

    return {"properties": props, "namespaces": namespaces}

def extract_metadata(filepath: str) -> Dict[str, Any]:
    filename = os.path.basename(filepath)
    tree = ET.parse(filepath)
    root = tree.getroot()

    mapping_info = get_mapping_for_file(filename)
    props = mapping_info["properties"]
    namespaces = mapping_info["namespaces"]
    # On fusionne namespaces avec TEI_NS
    ns = {**TEI_NS}
    for k, v in namespaces.items():
        ns[k] = v

    metadata = {
        "identifier": os.path.splitext(filename)[0],
        "filepath": filepath,
        "dublin_core": [],
        "extensions": [],
    }

    for term, xpath_expr in props.items():
        value = None
        if isinstance(xpath_expr, list):
            # Liste de xpath alternatifs
            for xp in xpath_expr:
                value = get_xpath_text(tree, xp, ns)
                if value:
                    break
        else:
            value = get_xpath_text(tree, xpath_expr, ns)

        if value:
            # Sépare dc: / ex: etc.
            if term.startswith("dc:"):
                metadata["dublin_core"].append(DublinCore(term=term[3:], value=value))
                metadata[term[3:]] = value
            elif term.startswith("ex:"):
                metadata["extensions"].append(Extension(term=term[3:], value=value))
                metadata[term[3:]] = value
            else:
                metadata[term] = value

    # Extrait aussi certains items supplémentaires du TEI si présents
    for item in root.findall(".//tei:fileDesc//tei:sourceDesc//tei:list[@type='metadata']/tei:item", TEI_NS):
        key = item.attrib.get("type", "").strip().lower()
        value = item.text.strip() if item.text else "unknown"
        if key and key not in metadata:
            metadata[key] = value

    return metadata

# === BUILD XML ELEMENTS ===

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
    for dc in res["dublin_core"]:
        el = ET.SubElement(dc_el, dc.term)
        el.set("xmlns", DC_NS)
        el.text = dc.value

    ext_el = ET.SubElement(res_el, "extensions")
    for ext in res["extensions"]:
        tag = ext.term if ext.term != "serie" else "serie"
        ns = DC_NS if ext.term.startswith("dc") else EXP_NS
        el = ET.SubElement(ext_el, tag)
        el.set("xmlns", ns)
        el.text = ext.value

    return res_el

def build_collection_element(identifier: str, title: str, description: Optional[str] = None,
                             is_reference: bool = False, filepath: Optional[str] = None) -> ET.Element:
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

def write_index_file(path: str, identifier: str, title: str, description: Optional[str],
                     member_elements: List[ET.Element]):
    ensure_dir(path)
    col_el = build_collection_element(identifier, title, description)
    members_el = ET.SubElement(col_el, "members")
    for m in member_elements:
        members_el.append(m)
    tree = ET.ElementTree(col_el)
    tree.write(os.path.join(path, "index.xml"), encoding="utf-8", xml_declaration=True)

# === BUILD INDEX.JSON ===

def compute_config_hash(*paths: List[str]) -> str:
    hasher = hashlib.sha256()
    for path in paths:
        with open(path, 'rb') as f:
            hasher.update(f.read())
    return hasher.hexdigest()

def write_build_index(output_path: str, files: List[str], config_paths: List[str]):
    build_data = {
        "config_version": compute_config_hash(*config_paths),
        "files": {}
    }
    for filepath in files:
        abs_path = os.path.abspath(filepath)
        if os.path.exists(abs_path):
            mtime = os.path.getmtime(abs_path)
            rel_path = os.path.relpath(abs_path, BASE_DIR).replace(os.sep, "/")
            build_data["files"][rel_path] = {"mtime": mtime}
    with open(os.path.join(output_path, "build_index.json"), "w", encoding="utf-8") as f:
        json.dump(build_data, f, indent=2, ensure_ascii=False)

# === MAIN ===

def main():
    ensure_dir(catalog_dir)
    previous_cache = load_cache()
    current_cache = {}
    resources = []

    # Collecte les fichiers TEI à traiter
    tei_files = [
        fn for fn in sorted(os.listdir(tei_dir))
        if fn.startswith("WORK_") and fn.endswith(".xml")
    ]

    # Mise à jour cache, extraction metadata si fichier modifié
    for fn in tei_files:
        path = os.path.join(tei_dir, fn)
        mtime = os.path.getmtime(path)
        current_cache[fn] = mtime
        if previous_cache.get(fn) == mtime:
            continue
        resources.append(extract_metadata(path))

    # Ajoute les fichiers non modifiés déjà en cache
    for fn, mtime in previous_cache.items():
        if fn not in current_cache:
            continue
        if fn not in [r["identifier"] + ".xml" for r in resources]:
            path = os.path.join(tei_dir, fn)
            resources.append(extract_metadata(path))

    # Organisation en collections hiérarchiques
    corpora = defaultdict(list)
    for res in resources:
        corpus_key = clean_id_with_strip(res.get("corpus", "unknown"))
        corpora[corpus_key].append(res)

    root_members = []
    for corpus_key, corpus_items in corpora.items():
        corpus_name = corpus_items[0].get("corpus", corpus_key)
        corpus_path = os.path.join(catalog_dir, "corpus", corpus_key)

        ags = defaultdict(list)
        for r in corpus_items:
            ag_key = clean_id_with_strip(r.get("authorgroup", "unknown"))
            ags[ag_key].append(r)

        ag_members = []
        for ag_key, ag_items in ags.items():
            ag_path = os.path.join(corpus_path, "authorgroup", ag_key)

            authors = defaultdict(list)
            for r in ag_items:
                author_key = clean_id_with_strip(r.get("author", "unknown"))
                authors[author_key].append(r)

            author_members = []
            for author_key, author_items in authors.items():
                author_path = os.path.join(ag_path, "author", author_key)
                res_members = [build_resource_element(r, os.path.relpath(r["filepath"], author_path)) for r in author_items]
                write_index_file(author_path, author_key, author_items[0].get("author", author_key), None, res_members)
                author_members.append(build_collection_element(author_key, author_items[0].get("author", author_key), is_reference=True, filepath=os.path.relpath(os.path.join(author_path, "index.xml"), ag_path)))

            write_index_file(ag_path, ag_key, ag_items[0].get("authorgroup", ag_key), None, author_members)
            ag_members.append(build_collection_element(ag_key, ag_items[0].get("authorgroup", ag_key), is_reference=True, filepath=os.path.relpath(os.path.join(ag_path, "index.xml"), corpus_path)))

        write_index_file(corpus_path, corpus_key, corpus_name, None, ag_members)
        root_members.append(build_collection_element(corpus_key, corpus_name, is_reference=True, filepath=os.path.relpath(os.path.join(corpus_path, "index.xml"), catalog_dir)))

    # Racine catalogue
    write_index_file(catalog_dir, "catalog", "Catalogue", None, root_members)

    # Mise à jour du cache
    save_cache(current_cache)

    # Génération build_index.json
    tei_paths = [os.path.join(tei_dir, fn) for fn in tei_files]
    write_build_index(BASE_DIR, tei_paths, [CONFIG_PATH, MAPPING_PATH])

if __name__ == "__main__":
    main()
