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


def unique_filename(base_name: str, existing_names: set[str], exclude: str = None) -> str:
    cleaned_names = existing_names.copy()
    if exclude:
        cleaned_names.discard(exclude)

    candidate = base_name
    i = 2
    while candidate in cleaned_names:
        candidate = f"{base_name}_{i}"
        i += 1
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
            log(f"[DEBUG] Niveau modifié à {i}: Ancien {old.get(key)} vs Nouveau {new.get(key)}")
            return i  # Retourne le premier niveau modifié
    return len(config["hierarchy"]) - 1  # Aucun changement détecté, retourne le dernier niveau


def build_resource_element(res: Dict[str, Any], relpath: str) -> ET.Element:
    res_el = ET.Element("resource", {
        "identifier": res["identifier"],
        "filepath": relpath.replace(os.sep, "/")
    })
    for lang, val in res.get("title", {"und": "Titre inconnu"}).items():
        t = ET.SubElement(res_el, "title")
        t.text = val
        if lang:
            t.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    for key, tag in [("description", "description"), ("creator", "author"), ("work", "work")]:
        if key in res:
            for lang, val in res[key].items():
                el = ET.SubElement(res_el, tag)
                el.text = val
                if lang:
                    el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    dc_el = ET.SubElement(res_el, "dublinCore");
    dc_ns = get_namespace("dc")
    for dc in res.get("dublin_core", []):
        el = ET.SubElement(dc_el, f"{{{dc_ns}}}{dc.term}")
        el.text = dc.value
        if dc.language:
            el.set("{http://www.w3.org/XML/1998/namespace}lang", dc.language)
    ext_el = ET.SubElement(res_el, "extensions");
    ex_ns = get_namespace("ex")
    for ext in res.get("extensions", []):
        tag = ext.term.split("/")[-1] if ext.term != "serie" else "serie"
        el = ET.SubElement(ext_el, f"{{{ex_ns}}}{tag}")
        el.text = ext.value
        if ext.language:
            el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    return res_el


def build_collection_element(identifier: str, title: str, description: Optional[str] = None,
                             is_reference: bool = False, filepath: Optional[str] = None) -> ET.Element:
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
        group_path = os.path.join(parent_path, slug, group_id) if level < len(
            config["hierarchy"]) - 1 else os.path.join(parent_path, slug)

        if level == len(config["hierarchy"]) - 1:
            ensure_dir(group_path)

            # Ajoute les fichiers déjà existants dans le dossier cible
            existing_names = {Path(f).stem for f in os.listdir(group_path) if f.endswith(".xml")}

            for res in group_items:
                raw_title = res.get("workTitle") or res.get("title", {}).get("en") or "work"
                base_name = clean_id_with_strip(raw_title)

                # On exclut le nom courant si nécessaire (ex: changement de groupe)
                # Pour cela on extrait l'ancien nom (si connu)
                tei_abs_path = os.path.abspath(os.path.join(BASE_DIR, res["filepath"]))
                old_stem = Path(tei_abs_path).stem  # nom sans ".xml"

                filename = unique_filename(base_name, existing_names, exclude=old_stem)
                full_filename = filename + ".xml"
                filepath = os.path.join(group_path, full_filename)

                # Ajout au set pour éviter doublons
                existing_names.add(filename)

                rel_path_to_tei = os.path.relpath(tei_abs_path, start=group_path).replace(os.sep, "/")
                res_el = build_resource_element(res, rel_path_to_tei)
                ET.ElementTree(res_el).write(filepath, encoding="utf-8")

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
                        filepath=os.path.relpath(os.path.join(group_path, "index.xml"), start=parent_path).replace(
                            os.sep, "/")
                    ))
                else:
                    log(f"[IGNORÉ] Pas de changement dans la collection : {group_path}")

    if attach_to_parent_items:
        members += recursive_group(level + 1, parent_path, attach_to_parent_items, parent_id)

    return members


def clean_empty_directories_and_indexes(path: str):
    """
    Supprime récursivement les dossiers vides et les fichiers index.xml inutiles,
    en remontant jusqu’à la racine du catalogue.
    Ajout de logs détaillés pour tracer la détection des fichiers restants.
    Force la suppression des répertoires non vides si nécessaire.
    """
    while path != CATALOG_DIR and os.path.isdir(path):
        contents = os.listdir(path)
        # On regarde uniquement les fichiers XML autres que index.xml
        non_index_xml_files = [f for f in contents if f.endswith(".xml") and f != "index.xml"]

        log(f"[NETTOYAGE] Vérification du dossier : {path}")
        log(f"[NETTOYAGE] Contenu complet : {contents}")
        log(f"[NETTOYAGE] Fichiers XML autres que index.xml détectés : {non_index_xml_files}")

        if not non_index_xml_files:
            index_path = os.path.join(path, "index.xml")
            if os.path.isfile(index_path):
                os.remove(index_path)
                log(f"[NETTOYAGE] index.xml supprimé : {index_path}")
            else:
                log(f"[NETTOYAGE] index.xml non trouvé, rien à supprimer ici.")

            try:
                # Utilisation de shutil.rmtree() pour forcer la suppression
                shutil.rmtree(path)
                log(f"[NETTOYAGE] Dossier supprimé (même non vide) : {path}")
            except Exception as e:
                log(f"[NETTOYAGE] ÉCHEC suppression du dossier {path} : {e}")
                break
            path = os.path.dirname(path)
        else:
            log(f"[NETTOYAGE] Dossier NON supprimé car fichiers XML restants : {non_index_xml_files}")
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


def selective_recursive_group(level: int, start_level: int, parent_path: str, items: List[Dict[str, Any]],
                              parent_id: str) -> List[ET.Element]:
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
        group_path = os.path.join(parent_path, slug, group_id) if level < len(
            config["hierarchy"]) - 1 else os.path.join(parent_path, slug)

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


def detect_global_impact(changed_level: int, modified_files: List[Dict[str, Any]]) -> bool:
    """
    Détermine si la modification nécessite une régénération globale.
    """
    # Si le niveau touché est la racine ou un niveau critique, retourne True
    if changed_level == 0:
        return True

    # Vérifiez les dépendances ou impacts croisés
    for file in modified_files:
        if detect_changed_level(file["old_hierarchy"], file["new_hierarchy"]) == 0:
            return True

    return False

def remove_dir_recursive_verbose(path):
    if not os.path.exists(path):
        log(f"[SUPPRESSION] Le dossier {path} n'existe pas, aucune suppression nécessaire")
        return
    # Lister les fichiers et dossiers avant suppression
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                os.remove(file_path)
                log(f"[SUPPRESSION] Fichier supprimé : {file_path}")
            except Exception as e:
                log(f"[ERREUR] Impossible de supprimer le fichier {file_path} : {e}")
        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                os.rmdir(dir_path)
                log(f"[SUPPRESSION] Dossier supprimé : {dir_path}")
            except Exception as e:
                log(f"[ERREUR] Impossible de supprimer le dossier {dir_path} : {e}")
    # Enfin, supprimer le dossier racine
    try:
        os.rmdir(path)
        log(f"[SUPPRESSION] Dossier racine supprimé : {path}")
    except Exception as e:
        log(f"[ERREUR] Impossible de supprimer le dossier racine {path} : {e}")


def delete_folder_and_contents(path):
    if os.path.exists(path):
        shutil.rmtree(path)
        print(f"Dossier supprimé : {path}")
    else:
        print(f"Le dossier {path} n'existe pas, suppression ignorée.")


def delete_file_and_cleanup_upwards(path, base_dir):
    """Supprimer un fichier ou un dossier, puis nettoyer les répertoires parents vides jusqu'à base_dir."""

    def handle_remove_readonly(func, path, exc_info):
        try:
            os.chmod(path, stat.S_IWRITE)  # Enlever lecture seule
            func(path)
        except Exception as e:
            log(f"[ERREUR] Impossible de forcer la suppression de {path} : {e}")

    try:
        if os.path.isfile(path):
            os.remove(path)
            log(f"[SUPPRESSION] Fichier supprimé : {path}")
        elif os.path.isdir(path):
            shutil.rmtree(path, onerror=handle_remove_readonly)
            log(f"[SUPPRESSION] Dossier supprimé récursivement : {path}")
        else:
            log(f"[INFO] Rien à supprimer : {path} n'existe pas ou n'est pas un fichier/dossier.")

        # Nettoyage des répertoires vides vers le haut
        parent_dir = os.path.dirname(path)
        while os.path.abspath(parent_dir) != os.path.abspath(base_dir):
            try:
                if not os.listdir(parent_dir):
                    os.rmdir(parent_dir)
                    log(f"[NETTOYAGE] Répertoire vidé : {parent_dir}")
                else:
                    break  # Stop dès qu'un dossier n'est pas vide
            except Exception as e:
                log(f"[ERREUR] Impossible de nettoyer {parent_dir} : {e}")
                break
            parent_dir = os.path.dirname(parent_dir)

    except Exception as e:
        log(f"[ERREUR] Impossible de supprimer {path} : {e}")

# Fonction pour nettoyer et supprimer un répertoire
# Fonction pour nettoyer et supprimer récursivement les répertoires vides et fichiers
def clean_and_remove_directory(directory):
    """Supprime récursivement un répertoire et son contenu sans hardcoder les chemins."""
    print(f"[INFO] Tentative de nettoyage et suppression du répertoire : {directory}")

    if os.path.exists(directory):
        for root, dirs, files in os.walk(directory, topdown=False):
            print(f"[INFO] Parcours du répertoire {root}... Contenu : {files}")

            # Suppression des fichiers XML autres que index.xml
            for file in files:
                if file.endswith(".xml") and file != "index.xml":
                    file_path = os.path.join(root, file)
                    try:
                        os.remove(file_path)
                        print(f"[SUPPRESSION] Fichier supprimé : {file_path}")
                    except Exception as e:
                        print(f"[ERREUR] Impossible de supprimer {file_path}: {e}")

            # Suppression des sous-dossiers vides (ex. works)
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                try:
                    os.rmdir(dir_path)
                    print(f"[SUPPRESSION] Dossier supprimé : {dir_path}")
                except Exception as e:
                    print(f"[ERREUR] Impossible de supprimer {dir_path}: {e}")

        # Après avoir nettoyé tout, on tente de supprimer le dossier principal
        try:
            os.rmdir(directory)  # Ou shutil.rmtree(directory) pour forcer la suppression
            print(f"[SUPPRESSION] Dossier supprimé : {directory}")
        except Exception as e:
            print(f"[ERREUR] Impossible de supprimer le dossier {directory}: {e}")
    else:
        print(f"[INFO] Le répertoire {directory} n'existe pas.")



def find_xml_files_in_subfolder(directory):
    """Trouve tous les fichiers XML dans le dossier, y compris les sous-dossiers."""
    xml_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".xml") and file != "index.xml":  # Ignorer index.xml
                xml_files.append(os.path.join(root, file))
    return xml_files

# Fonction générique pour supprimer les fichiers au niveau d'un sous-dossier
def delete_files_for_changed_level(level: int, hierarchy: Dict[str, Any], old_hierarchy: Dict[str, Any],
                                   group_path: str):
    """Supprime les fichiers d'un niveau affecté dans la hiérarchie sans hardcoder les chemins."""
    if is_parent_changed(group_path, hierarchy):
        delete_generated_files(old_hierarchy)  # Supprimer uniquement les fichiers du sous-niveau affecté
        log(f"[SUPPRESSION] Fichiers supprimés au niveau {level} pour {group_path}")

def delete_generated_files_by_group(group_path):
    """Supprime les fichiers générés pour un groupe donné de manière dynamique."""
    if os.path.exists(group_path):
        print(f"[SUPPRESSION] Nettoyage du groupe : {group_path}")
        delete_files_for_changed_level(0, {}, {}, group_path)
        clean_and_remove_directory(group_path)
    else:
        print(f"[INFO] Le groupe {group_path} n'existe pas, aucune suppression nécessaire.")

def find_xml_files_and_structure(base_dir):
    xml_files_by_dir = {}

    # Parcours récursif de tous les fichiers et dossiers dans le répertoire de base
    for root, dirs, files in os.walk(base_dir):
        # Liste des fichiers XML mais sans inclure "index.xml"
        xml_files = [file for file in files if file.endswith(".xml") and file != "index.xml"]

        # Si des fichiers XML (autres que index.xml) sont trouvés dans ce répertoire, on les ajoute au dictionnaire
        if xml_files:
            # On stocke le chemin du répertoire comme clé et la liste des fichiers XML comme valeur
            xml_files_by_dir[root] = xml_files

    return xml_files_by_dir

def delete_directory(path):
    """Supprimer le répertoire et tout son contenu."""
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
            log(f"[SUPPRESSION] Dossier supprimé : {path}")
        except Exception as e:
            log(f"[ERREUR] Impossible de supprimer {path} : {e}")
    else:
        log(f"[INFO] Le répertoire {path} n'existe pas, suppression ignorée.")


def count_non_index_xml_in_works(group_path: str) -> int:
    """
    Compte les fichiers XML (hors index.xml) dans le sous-dossier 'works' d'un group_path donné,
    et dans tous ses sous-dossiers.
    """
    # Vérifie d'abord si le dossier 'works' existe
    works_path = os.path.join(group_path, "works")
    if not os.path.exists(works_path):
        return 0

    # Utilisation de os.walk pour parcourir tous les sous-dossiers
    xml_count = 0
    for root, dirs, files in os.walk(works_path):
        for file in files:
            if file.endswith(".xml") and file.lower() != "index.xml":
                xml_count += 1

    return xml_count

