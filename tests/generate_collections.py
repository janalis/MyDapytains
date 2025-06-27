import os
import json
import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
import shutil
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple
from extract_metadata import extract_metadata

def log(message: str):
    print(f"[INFO] {message}")

def log_section(title: str):
    print("\n" + "=" * 50)
    print(f"{title}")
    print("=" * 50)

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

def make_json_serializable(obj: Any) -> Any:
    if isinstance(obj, list):
        return [make_json_serializable(e) for e in obj]
    elif isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif hasattr(obj, "__dict__"):
        return make_json_serializable(obj.__dict__)
    else:
        return obj

def extract_hierarchy(metadata: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, str]:
    h = {}
    for level in config["hierarchy"]:
        key = level["key"].split(":")[-1]
        val = metadata.get(key)
        if isinstance(val, dict):
            h[key] = clean_id_with_strip(val.get("en", ""))
        elif val:
            h[key] = clean_id_with_strip(val)
    return h

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
    return {"config_hash": None, "files": {}}

def save_state(state: Dict[str, Any]):
    state_to_save = {
        "config_hash": state.get("config_hash"),
        "files": state.get("files", {})
    }
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state_to_save, f, indent=2, ensure_ascii=False)

def delete_all_generated_content():
    if os.path.isdir(CATALOG_DIR):
        shutil.rmtree(CATALOG_DIR)
        log(f"[SUPPRESSION COMPLÈTE] Contenu de {CATALOG_DIR} supprimé")
    ensure_dir(CATALOG_DIR)

def delete_generated_files(hierarchy: Dict[str, str]):
    parts = [CATALOG_DIR]
    for lvl in config["hierarchy"]:
        key = lvl["key"].split(":")[-1]
        slug = lvl["slug"]
        if key not in hierarchy:
            break
        parts.extend([slug, hierarchy[key]])

    out = os.path.join(*parts)

    # Si c'est un dossier de collection (niveau intermédiaire)
    if os.path.isdir(out):
        shutil.rmtree(out)
        log(f"[SUPPRESSION] {out} supprimé")
        clean_empty_directories(os.path.dirname(out))
    else:
        # Sinon, on vérifie si c'est un fichier XML de ressource (niveau final)
        parent_dir = os.path.join(*parts[:-1])
        slug = config["hierarchy"][-1]["slug"]
        target_dir = os.path.join(parent_dir, slug)
        if os.path.isdir(target_dir):
            for file in os.listdir(target_dir):
                if file.endswith(".xml"):
                    os.remove(os.path.join(target_dir, file))
                    log(f"[SUPPRESSION] Fichier supprimé : {os.path.join(target_dir, file)}")
            clean_empty_directories(target_dir)


def clean_empty_directories(path: str):
    while os.path.isdir(path) and not os.listdir(path) and path != CATALOG_DIR:
        os.rmdir(path)
        log(f"[NETTOYAGE] Dossier vide supprimé : {path}")
        path = os.path.dirname(path)

def detect_changed_level(old: Dict[str, str], new: Dict[str, str]) -> int:
    """
    Compare deux hiérarchies et retourne le niveau où le changement est détecté.
    """
    for i, level in enumerate(config["hierarchy"]):
        key = level["key"].split(":")[-1]
        if old.get(key) != new.get(key):
            return i  # Retourne le premier niveau modifié
    return len(config["hierarchy"]) - 1  # Aucun changement détecté, retourne le dernier niveau

def build_resource_element(res: Dict[str, Any], relpath: str) -> ET.Element:
    res_el = ET.Element("resource", {
        "identifier": res["identifier"],
        "filepath": relpath.replace(os.sep, "/")
    })
    for lang, val in res.get("title", {"und":"Titre inconnu"}).items():
        t = ET.SubElement(res_el, "title")
        t.text = val
        if lang:
            t.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    for key,tag in [("description","description"),("creator","author"),("work","work")]:
        if key in res:
            for lang, val in res[key].items():
                el = ET.SubElement(res_el, tag)
                el.text = val
                if lang:
                    el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    dc_el = ET.SubElement(res_el, "dublinCore"); dc_ns = get_namespace("dc")
    for dc in res.get("dublin_core", []):
        el = ET.SubElement(dc_el, f"{{{dc_ns}}}{dc.term}")
        el.text = dc.value
        if dc.language:
            el.set("{http://www.w3.org/XML/1998/namespace}lang", dc.language)
    ext_el = ET.SubElement(res_el, "extensions"); ex_ns = get_namespace("ex")
    for ext in res.get("extensions", []):
        tag = ext.term.split("/")[-1] if ext.term != "serie" else "serie"
        el = ET.SubElement(ext_el, f"{{{ex_ns}}}{tag}")
        el.text = ext.value
        if ext.language:
            el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    return res_el

def build_collection_element(identifier: str, title: str, description: Optional[str]=None,
                             is_reference: bool=False, filepath: Optional[str]=None) -> ET.Element:
    if is_reference and filepath:
        return ET.Element("collection", {"filepath": filepath.replace(os.sep, "/")})
    col_el = ET.Element("collection", {"identifier": identifier})
    ET.SubElement(col_el, "title").text = title
    if description:
        dc_el = ET.SubElement(col_el, "dublinCore")
        desc_el = ET.SubElement(dc_el, "description",
                                {"xmlns": get_namespace("dc")})
        desc_el.text = description
    return col_el

def write_index_file(path: str, identifier: str, title: str,
                     description: Optional[str], members: List[ET.Element]):
    ensure_dir(path)
    col = build_collection_element(identifier, title, description)
    mems = ET.SubElement(col, "members")
    for m in members:
        mems.append(m)
    tree = ET.ElementTree(col)
    tree.write(os.path.join(path, "index.xml"), encoding="utf-8", xml_declaration=True)
    log(f"[GÉNÉRATION] Collection : {os.path.join(path, 'index.xml')}")

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
                log(f"[GÉNÉRATION] Ressource : {filepath}")
            members.append(build_collection_element(
                identifier=group_identifier,
                title=f"{title_label} : {group_name}",
                is_reference=True,
                filepath=os.path.relpath(filepath, start=parent_path).replace(os.sep, "/")
            ))
        else:
            sub_members = recursive_group(level + 1, group_path, group_items, group_identifier)
            if sub_members:
                if is_parent_changed(group_path, sub_members):
                    # Écrire un nouvel index si les membres ont changé
                    write_index_file(group_path, group_identifier, f"{title_label} : {group_name}", None, sub_members)
                    members.append(build_collection_element(
                        identifier=group_identifier,
                        title=f"{title_label} : {group_name}",
                        is_reference=True,
                        filepath=os.path.relpath(os.path.join(group_path, "index.xml"), start=parent_path).replace(os.sep, "/")
                    ))
                else:
                    log(f"[IGNORÉ] Pas de changement dans la collection : {group_path}")

    if attach_to_parent_items:
        members += recursive_group(level + 1, parent_path, attach_to_parent_items, parent_id)

    return members

# ...
# (Tout le code avant la fonction main reste identique)

def clean_empty_directories_and_indexes(path: str):
    """
    Supprime récursivement les dossiers vides et les fichiers index.xml inutiles.
    """
    while path != CATALOG_DIR and os.path.isdir(path):
        contents = os.listdir(path)
        non_index_files = [f for f in contents if f != "index.xml"]
        if not non_index_files:
            index_path = os.path.join(path, "index.xml")
            if os.path.isfile(index_path):
                os.remove(index_path)
                log(f"[NETTOYAGE] index.xml supprimé : {index_path}")
            os.rmdir(path)
            log(f"[NETTOYAGE] Dossier vide supprimé : {path}")
            path = os.path.dirname(path)
        else:
            break

def delete_generated_files_by_path(output_path: str):  # MOD
    """
    Supprime un fichier généré (ressource ou index) et nettoie les répertoires en amont.
    """
    abs_path = os.path.join(BASE_DIR, output_path)
    if os.path.isfile(abs_path):
        os.remove(abs_path)
        log(f"[SUPPRESSION] Fichier supprimé : {abs_path}")
    dir_path = os.path.dirname(abs_path)
    clean_empty_directories_and_indexes(dir_path)
    
def selective_recursive_group(level: int, start_level: int, parent_path: str, items: List[Dict[str, Any]], parent_id: str) -> List[ET.Element]:
    """
    Génère un groupe hiérarchique à partir d'un niveau spécifique au lieu de la racine.
    """
    if level < start_level:
        return []  # Ignorer les niveaux en dessous du point de départ

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
                log(f"[GÉNÉRATION] Ressource : {filepath}")
            members.append(build_collection_element(
                identifier=group_identifier,
                title=f"{title_label} : {group_name}",
                is_reference=True,
                filepath=os.path.relpath(filepath, start=parent_path).replace(os.sep, "/")
            ))
        else:
            sub_members = selective_recursive_group(level + 1, start_level, group_path, group_items, group_identifier)
            if sub_members:
                if is_parent_changed(group_path, sub_members):
                    # Écrire un nouvel index si les membres ont changé
                    write_index_file(group_path, group_identifier, f"{title_label} : {group_name}", None, sub_members)
                    members.append(build_collection_element(
                        identifier=group_identifier,
                        title=f"{title_label} : {group_name}",
                        is_reference=True,
                        filepath=os.path.relpath(os.path.join(group_path, "index.xml"), start=parent_path).replace(
                            os.sep, "/")
                    ))
                else:
                    log(f"[IGNORÉ] Pas de changement dans la collection : {group_path}")

    if attach_to_parent_items:
        members += selective_recursive_group(level + 1, start_level, parent_path, attach_to_parent_items, parent_id)

    return members

def is_parent_changed(parent_path: str, current_members: List[ET.Element]) -> bool:
    """
    Détermine si une collection parentale a changé en comparant ses membres.
    """
    index_file = os.path.join(parent_path, "index.xml")
    if not os.path.exists(index_file):
        return True  # Pas de fichier existant, donc changement

    # Charger l'arborescence XML existante
    old_tree = ET.parse(index_file)
    old_members = [member.attrib.get("filepath") for member in old_tree.findall(".//members/collection")]
    old_members += [member.attrib.get("filepath") for member in old_tree.findall(".//members/resource")]

    # Comparer les anciens et nouveaux membres
    new_members = [m.attrib.get("filepath") for m in current_members]
    return set(old_members) != set(new_members)

def regenerate_from_level(changed_level: int, resources: List[Dict[str, Any]]):
    """
    Régénération sélective à partir d'un niveau de hiérarchie spécifique.
    """
    log_section(f"Régénération ciblée à partir du niveau {changed_level}")

    members = selective_recursive_group(changed_level, changed_level, CATALOG_DIR, resources, "")
    parent_path = CATALOG_DIR

    # Remonter progressivement la hiérarchie en mise à jour conditionnelle des parents
    for level in range(changed_level - 1, -1, -1):
        current_config = config["hierarchy"][level]
        slug = current_config["slug"]
        parent_path = os.path.join(parent_path, slug)

        if is_parent_changed(parent_path, members):
            log(f"[MISE À JOUR] Collection parent à régénérer : {parent_path}")
            write_index_file(parent_path, slug, current_config["title"], None, members)
        else:
            log(f"[IGNORÉ] Pas de changement : {parent_path}")

        # Créer une nouvelle liste de membres pour le niveau au-dessus
        members = [
            {"filepath": os.path.relpath(os.path.join(parent_path, "index.xml"), BASE_DIR)}
        ]
        
def main():
    ensure_dir(CATALOG_DIR)
    current_hash = compute_config_hash(CONFIG_PATH, MAPPING_PATH)
    state = load_state()
    previous_hash = state.get("config_hash")
    previous_files = state.get("files", {})
    current_files = {}

    process_all = current_hash != previous_hash
    added, modified, deleted = [], [], []

    log_section("Vérification de l'état de la configuration et des fichiers")
    if process_all:
        log("La configuration a changé : régénération complète requise.")
        delete_all_generated_content()
        previous_files = {}

    for fn in os.listdir(TEI_DIR):
        if not (fn.startswith("WORK_") and fn.endswith(".xml")):
            continue
        abs_path = os.path.abspath(os.path.join(TEI_DIR, fn))
        rel = os.path.relpath(abs_path, BASE_DIR)
        rel = os.path.normpath(rel).replace(os.sep, "/")
        mtime = os.path.getmtime(abs_path)
        current_files[rel] = {"mtime": mtime}

        res = extract_metadata(abs_path)
        hierarchy = extract_hierarchy(res, config)
        current_files[rel].update({"hierarchy": hierarchy})

        prev_entry = previous_files.get(rel)
        if not prev_entry or process_all:
            added.append((rel, res))
            log(f"[AJOUTÉ] {rel} (nouveau ou tout régénéré)")
        elif prev_entry["hierarchy"] != hierarchy:
            modified.append((rel, res))
            log(f"[HIERARCHIE MODIFIÉE] {rel} (hiérarchie changée)")
        elif prev_entry["mtime"] != mtime:
            modified.append((rel, res))
            log(f"[MODIFIÉ] {rel} (contenu modifié, hiérarchie identique)")
        else:
            current_files[rel]["output_filepath"] = prev_entry.get("output_filepath")
            log(f"[IGNORÉ] {rel} inchangé")

    deleted = [] if process_all else [p for p in previous_files if p not in current_files]
    if deleted:
        log(f"[SUPPRIMÉ] {deleted}")

    log_section("Suppression des fichiers supprimés")
    for rel in deleted:
        output_path = previous_files[rel].get("output_filepath")
        if output_path:
            delete_generated_files_by_path(output_path)

    log_section("Mise à jour des fichiers de ressources sans changement hiérarchique")

    # Initialisation de la variable pour éviter les problèmes d'accès sans définition
    resource_hierarchy_unchanged = False

    for rel, res in modified:
        prev_entry = previous_files.get(rel)
        if prev_entry and prev_entry["hierarchy"] == extract_hierarchy(res, config):
            output_path = prev_entry.get("output_filepath")
            if output_path:
                abs_output_path = os.path.join(BASE_DIR, output_path)
                parent_dir = os.path.dirname(abs_output_path)
                ensure_dir(parent_dir)

                tei_abs_path = os.path.abspath(os.path.join(BASE_DIR, rel))
                rel_path_to_tei = os.path.relpath(tei_abs_path, start=parent_dir).replace(os.sep, "/")

                res["filepath"] = rel
                res_el = build_resource_element(res, rel_path_to_tei)
                ET.ElementTree(res_el).write(abs_output_path, encoding="utf-8", xml_declaration=True)
                log(f"[MISE À JOUR] Ressource mise à jour sans modification de hiérarchie : {abs_output_path}")

            # Mettre à jour l'état current_files pour inclure mtime et le chemin de sortie
            current_files[rel]["mtime"] = os.path.getmtime(tei_abs_path)  # Actualisation du mtime
            current_files[rel]["output_filepath"] = output_path

            resource_hierarchy_unchanged = True

    # Sauvegarder l'état si des ressources ont été mises à jour
    if resource_hierarchy_unchanged:
        state = {
            "config_hash": current_hash,
            "files": current_files
        }
        save_state(state)
        log("[MISE À JOUR] build_state.json mis à jour après ressources sans changement de hiérarchie.")

    log_section("Chargement des ressources pour régénération")
    resources_for_recursive_group = []
    for rel, res in added + modified:
        prev_entry = previous_files.get(rel, {})
        if prev_entry.get("hierarchy") != extract_hierarchy(res, config):
            res["filepath"] = rel
            resources_for_recursive_group.append(res)

    output_paths_by_rel = {}

    def track_output_filepath(filepath: str, rel: str):
        output_paths_by_rel[rel] = filepath.replace(os.sep, "/")

    if resources_for_recursive_group or deleted or process_all:
        log_section("Régénération sélective de l'arborescence")
        for rel, res in modified:
            prev_entry = previous_files.get(rel)
            if prev_entry:
                old_hierarchy = prev_entry.get("hierarchy", {})
                new_hierarchy = extract_hierarchy(res, config)
                changed_level = detect_changed_level(old_hierarchy, new_hierarchy)

                log(f"[MODIFICATION] Niveaux impactés pour {rel} : {changed_level}")

                if changed_level == 0:
                    # Niveau racine modifié, régénération complète requise
                    log(f"[MODIFICATION] Régénération complète requise à partir de la racine pour {rel}")
                    delete_all_generated_content()
                else:
                    # Suppression des descendants affectés et régénération partielle
                    delete_generated_files(old_hierarchy)
                    log(f"[MODIFICATION] Régénération à partir du niveau {changed_level} pour {rel}")
                    # Ajouter le fichier aux ressources pour régénération
                    resources_for_recursive_group.append(res)

        def recursive_group_tracked(level: int, parent_path: str, items: List[Dict[str, Any]], parent_id: str) -> List[ET.Element]:
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
                        log(f"[GÉNÉRATION] Ressource : {filepath}")
                        track_output_filepath(os.path.relpath(filepath, BASE_DIR), res["filepath"])
                    members.append(build_collection_element(
                        identifier=group_identifier,
                        title=f"{title_label} : {group_name}",
                        is_reference=True,
                        filepath=os.path.relpath(filepath, start=parent_path).replace(os.sep, "/")
                    ))
                else:
                    sub_members = recursive_group_tracked(level + 1, group_path, group_items, group_identifier)
                    if sub_members:
                        if is_parent_changed(group_path, sub_members):
                            # Écrire un nouvel index si les membres ont changé
                            write_index_file(group_path, group_identifier, f"{title_label} : {group_name}", None, sub_members)
                            members.append(build_collection_element(
                                identifier=group_identifier,
                                title=f"{title_label} : {group_name}",
                                is_reference=True,
                                filepath=os.path.relpath(os.path.join(group_path, "index.xml"), start=parent_path).replace(os.sep, "/")
                            ))
                        else:
                            log(f"[IGNORÉ] Pas de changement dans la collection : {group_path}")

            if attach_to_parent_items:
                members += recursive_group_tracked(level + 1, parent_path, attach_to_parent_items, parent_id)

            return members

        members = recursive_group_tracked(0, CATALOG_DIR, resources_for_recursive_group, "")
        if members:
            write_index_file(CATALOG_DIR, "root", "Catalogue principal", None, members)

        for rel in current_files:
            if rel in output_paths_by_rel:
                current_files[rel]["output_filepath"] = output_paths_by_rel[rel]

        state = {
            "config_hash": current_hash,
            "files": current_files
        }
        save_state(state)
        log("[MISE À JOUR] build_state.json mis à jour")

if __name__ == "__main__":
    main()