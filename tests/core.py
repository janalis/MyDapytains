import os
import json
import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
import shutil
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple
from extract_metadata import extract_metadata  # ok si plus d'import circulaire via utils.py
from utils import get_namespace

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

# Plus de gestion namespaces ici, elle est déplacée dans utils.py

REQUIRED_NAMESPACES = [ns.split(":")[0] for h in config["hierarchy"] for ns in [h["key"]]]


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
    xml_ns = get_namespace("xml")  # récupère le namespace xml depuis ta config

    res_el = ET.Element("resource", {
        "identifier": res["identifier"],
        "filepath": relpath.replace(os.sep, "/")
    })

    for lang, val in res.get("title", {"und": "Titre inconnu"}).items():
        t = ET.SubElement(res_el, "title")
        t.text = val
        if lang:
            t.set(f"{{{xml_ns}}}lang", lang)

    for key, tag in [("description", "description"), ("creator", "author"), ("work", "work")]:
        if key in res:
            for lang, val in res[key].items():
                el = ET.SubElement(res_el, tag)
                el.text = val
                if lang:
                    el.set(f"{{{xml_ns}}}lang", lang)

    dc_el = ET.SubElement(res_el, "dublinCore")
    dc_ns = get_namespace("dc")
    for dc in res.get("dublin_core", []):
        el = ET.SubElement(dc_el, f"{{{dc_ns}}}{dc.term}")
        el.text = dc.value
        if dc.language:
            el.set(f"{{{xml_ns}}}lang", dc.language)

    ext_el = ET.SubElement(res_el, "extensions")
    ex_ns = get_namespace("ex")
    for ext in res.get("extensions", []):
        tag = ext.term.split("/")[-1] if ext.term != "serie" else "serie"
        el = ET.SubElement(ext_el, f"{{{ex_ns}}}{tag}")
        el.text = ext.value
        # Petite correction ici : tu dois utiliser ext.language (pas lang)
        if ext.language:
            el.set(f"{{{xml_ns}}}lang", ext.language)

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

def clean_empty_directories_and_indexes(path: str, modified_level: int):
    """
    Supprime récursivement les dossiers vides et les fichiers index.xml inutiles,
    mais ne supprime pas de dossiers au-dessus du niveau modifié.

    :param path: Le chemin du dossier à nettoyer.
    :param modified_level: Le niveau à partir duquel la suppression doit s'arrêter.
    """
    print("APPEL A LA FONCTION CLEAN EMPTY DIRECTORIES AND INDEXES !!!!")

    while path != CATALOG_DIR and os.path.isdir(path):
        # Calcul du niveau actuel par rapport à la racine du catalogue (niveau 0)
        current_level = len(os.path.relpath(path, CATALOG_DIR).split(os.sep))

        # Si le niveau actuel est supérieur au niveau modifié, on arrête la suppression
        if current_level <= modified_level:
            log(f"[NETTOYAGE] Arrêt de la suppression, niveau {current_level} supérieur au niveau modifié {modified_level}.")
            break

        print(f"current_level: {current_level}, modified_level: {modified_level}")

        contents = os.listdir(path)
        non_index_xml_files = [f for f in contents if f.endswith(".xml") and f != "index.xml"]

        log(f"[NETTOYAGE] Vérification du dossier : {path}")
        log(f"[NETTOYAGE] Contenu complet : {contents}")
        log(f"[NETTOYAGE] Fichiers XML autres que index.xml détectés : {non_index_xml_files}")

        # Si le dossier ne contient que des index.xml ou est vide, on peut le supprimer
        if not non_index_xml_files:
            index_path = os.path.join(path, "index.xml")
            if os.path.isfile(index_path):
                os.remove(index_path)
                log(f"[NETTOYAGE] index.xml supprimé : {index_path}")
            else:
                log(f"[NETTOYAGE] index.xml non trouvé, rien à supprimer ici.")

            # Vérification si le dossier est vide après suppression de l'index.xml
            if not os.listdir(path):  # Si le dossier est vide
                try:
                    shutil.rmtree(path)
                    log(f"[NETTOYAGE] Dossier supprimé : {path}")
                except Exception as e:
                    log(f"[NETTOYAGE] ÉCHEC suppression du dossier {path} : {e}")
                    break
            else:
                log(f"[NETTOYAGE] Dossier non supprimé, reste des fichiers.")
        else:
            log(f"[NETTOYAGE] Dossier NON supprimé car fichiers XML restants : {non_index_xml_files}")
            break  # Ne pas supprimer ce dossier, on s'arrête ici.

        # Remonter d'un niveau dans l'arborescence
        path = os.path.dirname(path)


def delete_generated_files_by_path(output_path: str):  # MOD
    """
    Supprime un fichier généré (ressource ou index) et nettoie les répertoires en amont.
    """
    abs_path = os.path.join(BASE_DIR, output_path)
    if os.path.isfile(abs_path):
        os.remove(abs_path)
        log(f"[SUPPRESSION] Fichier supprimé : {abs_path}")
    dir_path = os.path.dirname(abs_path)
    clean_empty_directories_and_indexes(dir_path, 0)

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

# Fonction générique pour supprimer les fichiers au niveau d'un sous-dossier
def delete_files_for_changed_level(level: int, hierarchy: Dict[str, Any], old_hierarchy: Dict[str, Any],
                                   group_path: str):
    """Supprime les fichiers d'un niveau affecté dans la hiérarchie sans hardcoder les chemins."""
    if is_parent_changed(group_path, hierarchy):
        delete_generated_files(old_hierarchy)  # Supprimer uniquement les fichiers du sous-niveau affecté
        log(f"[SUPPRESSION] Fichiers supprimés au niveau {level} pour {group_path}")


def count_non_index_xml_recursively(base_path: str) -> int:
    if not os.path.exists(base_path):
        return 0
    return sum(
        1
        for root, _, files in os.walk(base_path)
        for file in files
        if file.endswith(".xml") and file.lower() != "index.xml"
    )