from core import *

def main():
    ensure_dir(CATALOG_DIR)
    log(f"[DEBUG] Répertoire {CATALOG_DIR} vérifié/créé.")
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

    log(f"[DEBUG] TEI_DIR = {TEI_DIR}")
    log(f"[DEBUG] BASE_DIR = {BASE_DIR}")
    files = os.listdir(TEI_DIR)
    log(f"[DEBUG] Fichiers dans {TEI_DIR}: {files}")

    for fn in os.listdir(TEI_DIR):
        if not (fn.startswith("WORK_") and fn.endswith(".xml")):
            continue
        abs_path = os.path.abspath(os.path.join(TEI_DIR, fn))
        rel = os.path.relpath(abs_path, BASE_DIR)
        rel = os.path.normpath(rel).replace(os.sep, "/")
        mtime = os.path.getmtime(abs_path)
        current_files[rel] = {"mtime": mtime}

        log(f"[DEBUG] previous_files = {previous_files}")
        log(f"[DEBUG] current_files = {current_files}")

        res = extract_metadata(abs_path)
        if not res:
            log(f"[ERREUR] Échec de l'extraction des métadonnées pour {abs_path}")
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
    resources_for_recursive_group = {}
    for rel, res in added + modified:
        prev_entry = previous_files.get(rel, {})
        if prev_entry.get("hierarchy") != extract_hierarchy(res, config):
            res["filepath"] = rel
            resources_for_recursive_group[rel] = res

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
                log(f"[DEBUG] changed_level = {changed_level} pour {rel}")
                if changed_level >= 0:
                    if changed_level == 0:
                        log(f"[MODIFICATION] Régénération complète pour {rel}")
                        delete_all_generated_content()
                    else:
                        log(f"[MODIFICATION] Suppression partielle et nettoyage pour {rel}")

                        # Construction du chemin complet de l'ancien dossier à supprimer
                        old_path_parts = []
                        for i, level_def in enumerate(config["hierarchy"]):
                            if i > changed_level:
                                break
                            key = level_def["key"].split(":")[-1]
                            value = old_hierarchy.get(key)
                            if not value:
                                break
                            clean_value = clean_id_with_strip(value.get("en") if isinstance(value, dict) else value)
                            old_path_parts.append(level_def["slug"])
                            old_path_parts.append(clean_value)

                        old_full_path = os.path.join(CATALOG_DIR, *old_path_parts)
                        log(f"[DEBUG] Chemin complet à supprimer (niveau {changed_level}) : {old_full_path}")

                        if os.path.exists(old_full_path):
                            # Récupère tous les fichiers .xml sauf index.xml, récursivement
                            xml_files_fullpaths = [
                                os.path.join(root, file)
                                for root, _, files in os.walk(old_full_path)
                                for file in files
                                if file.endswith(".xml") and file.lower() != "index.xml"
                            ]

                            # Récupère les noms de fichiers déplacés (sans extension)
                            moved_files_basenames = set()
                            for rel2, res2 in resources_for_recursive_group.items():
                                prev_entry2 = previous_files.get(rel2)
                                if prev_entry2:
                                    old_hierarchy2 = prev_entry2.get("hierarchy", {})
                                    old_path_parts2 = []
                                    for i, level_def in enumerate(config["hierarchy"]):
                                        if i > changed_level:
                                            break
                                        key = level_def["key"].split(":")[-1]
                                        value = old_hierarchy2.get(key)
                                        if not value:
                                            break
                                        clean_value = clean_id_with_strip(
                                            value.get("en") if isinstance(value, dict) else value
                                        )
                                        old_path_parts2.append(level_def["slug"])
                                        old_path_parts2.append(clean_value)
                                    candidate_path = os.path.join(CATALOG_DIR, *old_path_parts2)
                                    if os.path.normpath(candidate_path) == os.path.normpath(old_full_path):
                                        raw_title = res2.get("workTitle") or res2.get("title", {}).get("en") or "work"
                                        base_name = clean_id_with_strip(raw_title)
                                        moved_files_basenames.add(base_name)

                            # Séparer les fichiers déplacés des fichiers restants
                            remaining_files = [
                                fp for fp in xml_files_fullpaths
                                if os.path.splitext(os.path.basename(fp))[0] not in moved_files_basenames
                            ]

                            if len(remaining_files) == 0:
                                # Tous les fichiers ont été déplacés → supprimer le dossier complet
                                try:
                                    delete_file_and_cleanup_upwards(old_full_path, CATALOG_DIR)
                                    log(f"[SUPPRESSION] Dossier supprimé récursivement : {old_full_path}")
                                except Exception as e:
                                    log(f"[ERREUR] Impossible de supprimer {old_full_path} : {e}")
                            else:
                                # Supprimer uniquement les fichiers déplacés
                                for filepath in xml_files_fullpaths:
                                    basename = os.path.splitext(os.path.basename(filepath))[0]
                                    if basename in moved_files_basenames:
                                        try:
                                            delete_file_and_cleanup_upwards(filepath, CATALOG_DIR)
                                            log(f"[SUPPRESSION] Fichier supprimé : {filepath}")
                                        except Exception as e:
                                            log(f"[ERREUR] Impossible de supprimer {filepath} : {e}")

                                log(f"[SUPPRESSION] Dossier conservé : {len(remaining_files)} fichiers XML restants")

                                # Regénérer index.xml avec les fichiers restants
                                collection_members = []
                                for full_path in remaining_files:
                                    rel_path = os.path.relpath(full_path, start=old_full_path).replace(os.sep, "/")
                                    base = os.path.splitext(os.path.basename(full_path))[0]
                                    collection_members.append(build_collection_element(
                                        identifier=base,
                                        title=base,  # Ou extraire un vrai titre si dispo
                                        is_reference=True,
                                        filepath=rel_path
                                    ))

                                try:
                                    # Reconstruire correctement le group_identifier à partir des valeurs hiérarchiques uniquement (sans slugs)
                                    group_identifier_parts = []
                                    for i in range(changed_level + 1):
                                        level_conf = config["hierarchy"][i]
                                        key = level_conf["key"].split(":")[-1]
                                        value = old_hierarchy.get(key)
                                        if not value:
                                            continue
                                        group_identifier_parts.append(
                                            clean_id_with_strip(value.get("en") if isinstance(value, dict) else value)
                                        )
                                    group_identifier = "_".join(group_identifier_parts)

                                    # Même logique pour le titre
                                    level_def = config["hierarchy"][changed_level]
                                    title_label = level_def["title"]
                                    key = level_def["key"].split(":")[-1]
                                    value = old_hierarchy.get(key)
                                    group_name = value.get("en") if isinstance(value, dict) else value
                                    title = f"{title_label} : {group_name}" if group_name else title_label

                                    # Écriture du fichier index avec titre et identifiant corrigés
                                    write_index_file(old_full_path, group_identifier, title, None, collection_members)

                                    log(f"[MISE À JOUR] index.xml mis à jour dans : {old_full_path}")
                                except Exception as e:
                                    log(f"[ERREUR] Impossible de régénérer l'index.xml dans {old_full_path} : {e}")
                        else:
                            log(f"[ERREUR] Le dossier {old_full_path} n'existe pas")

                        # Nettoyage récursif du parent
                        parent_path = os.path.dirname(old_full_path)
                        log(f"[NETTOYAGE] Nettoyage à partir du dossier parent : {parent_path}")
                        if os.path.exists(parent_path):
                            clean_empty_directories_and_indexes(parent_path, changed_level)
                        else:
                            log(f"[NETTOYAGE] Le chemin {parent_path} n'existe PAS, nettoyage ignoré")

                        log(f"[MODIFICATION] Régénération à partir du niveau {changed_level} pour {rel}")
                        resources_for_recursive_group[rel] = res

        def recursive_group_tracked(level: int, parent_path: str, items: List[Dict[str, Any]], parent_id: str) -> List[
            ET.Element]:
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

            # Group items by the current level key
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

                    # --- Nettoyer les fichiers orphelins ---
                    expected_basenames = set()

                    # 1. Ajouter les fichiers générés dans cette passe
                    for res in group_items:
                        raw_title = res.get("workTitle") or res.get("title", {}).get("en") or "work"
                        base_name = clean_id_with_strip(raw_title)
                        expected_basenames.add(base_name)

                    # 2. Ajouter les fichiers existants non modifiés à partir de current_files
                    for rel_path, info in current_files.items():
                        output_path = info.get("output_filepath")
                        if not output_path:
                            continue
                        output_abs = os.path.abspath(os.path.join(BASE_DIR, output_path))
                        output_dir = os.path.dirname(output_abs)
                        if os.path.normpath(output_dir) == os.path.normpath(group_path):
                            file_basename = os.path.splitext(os.path.basename(output_path))[0]
                            expected_basenames.add(file_basename)

                    existing_files = [f for f in os.listdir(group_path) if
                                      f.endswith(".xml") and f.lower() != "index.xml"]

                    log(f"[DEBUG] Fichiers attendus dans {group_path} : {expected_basenames}")
                    log(f"[DEBUG] Fichiers trouvés dans {group_path} : {existing_files}")

                    # Supprimer les fichiers orphelins
                    for file in existing_files:
                        base = os.path.splitext(file)[0]
                        if base not in expected_basenames:
                            try:
                                os.remove(os.path.join(group_path, file))
                                log(f"[NETTOYAGE] Fichier orphelin supprimé : {file} dans {group_path}")
                            except Exception as e:
                                log(f"[ERREUR] Impossible de supprimer fichier orphelin {file} : {e}")

                    # Recharger noms après nettoyage
                    existing_names = set(os.path.splitext(f)[0] for f in os.listdir(group_path) if
                                         f.endswith(".xml") and f.lower() != "index.xml")

                    filename_map = {}
                    for res in group_items:
                        raw_title = res.get("workTitle") or res.get("title", {}).get("en") or "work"
                        base_name = clean_id_with_strip(raw_title)
                        filename_base = unique_filename(base_name, existing_names)
                        existing_names.add(filename_base)
                        filename_map[id(res)] = filename_base

                    generated_files = []
                    for res in group_items:
                        filename_base = filename_map[id(res)]
                        filename = filename_base + ".xml"
                        filepath = os.path.join(group_path, filename)

                        tei_abs_path = os.path.abspath(os.path.join(BASE_DIR, res["filepath"]))
                        rel_path_to_tei = os.path.relpath(tei_abs_path, start=group_path).replace(os.sep, "/")
                        res_el = build_resource_element(res, rel_path_to_tei)
                        ET.ElementTree(res_el).write(filepath, encoding="utf-8", xml_declaration=True)
                        log(f"[DEBUG] Fichier généré : {filepath}")

                        track_output_filepath(os.path.relpath(filepath, BASE_DIR), res["filepath"])
                        generated_files.append((filename_base, raw_title, filename))

                    # Ajouter les fichiers non modifiés déjà présents
                    current_generated_basenames = set(filename_map.values())
                    existing_files_map = {
                        os.path.splitext(f)[0]: os.path.join(group_path, f)
                        for f in os.listdir(group_path)
                        if f.endswith(".xml") and f.lower() != "index.xml" and os.path.splitext(f)[
                            0] not in current_generated_basenames
                    }

                    for existing_file, existing_path in existing_files_map.items():
                        members.append(build_collection_element(
                            identifier=existing_file,
                            title=existing_file,
                            is_reference=True,
                            filepath=os.path.relpath(existing_path, start=parent_path).replace(os.sep, "/")
                        ))

                    # Ajouter aussi les fichiers générés
                    for filename_base, raw_title, filename in generated_files:
                        filepath = os.path.join(group_path, filename)
                        members.append(build_collection_element(
                            identifier=filename_base,
                            title=raw_title,
                            is_reference=True,
                            filepath=os.path.relpath(filepath, start=parent_path).replace(os.sep, "/")
                        ))

                else:
                    sub_members = recursive_group_tracked(level + 1, group_path, group_items, group_identifier)
                    if sub_members:
                        if is_parent_changed(group_path, sub_members):
                            write_index_file(group_path, group_identifier, f"{title_label} : {group_name}", None,
                                             sub_members)
                            members.append(build_collection_element(
                                identifier=group_identifier,
                                title=f"{title_label} : {group_name}",
                                is_reference=True,
                                filepath=os.path.relpath(os.path.join(group_path, "index.xml"),
                                                         start=parent_path).replace(os.sep, "/")
                            ))
                        else:
                            log(f"[IGNORÉ] Pas de changement dans la collection : {group_path}")

            if attach_to_parent_items:
                sub_items = recursive_group_tracked(level + 1, parent_path, attach_to_parent_items, parent_id)
                if sub_items:
                    members += sub_items

            return members

        members = recursive_group_tracked(0, CATALOG_DIR, list(resources_for_recursive_group.values()), "")

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
        log("[MISE À JOUR] build_state.json a été mis à jour")

if __name__ == "__main__":
    main()