import os
import shutil

paths_to_remove = [
    "build_state.json",
    "tests/build_state.json",
    "tests/catalog/corpora",
    "tests/catalog/index.xml"
]

for path in paths_to_remove:
    if os.path.exists(path):
        try:
            if os.path.isfile(path):
                os.remove(path)
                print(f"Fichier supprimé : {path}")
            elif os.path.isdir(path):
                shutil.rmtree(path)
                print(f"Dossier supprimé : {path}")
        except Exception as e:
            print(f"Erreur lors de la suppression de {path} : {e}")
    else:
        print(f"Le chemin n'existe pas : {path}")
