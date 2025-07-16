# import os
#
#
# def find_xml_files_and_structure(base_dir):
#     xml_files_by_dir = {}
#
#     # Parcours récursif de tous les fichiers et dossiers dans le répertoire de base
#     for root, dirs, files in os.walk(base_dir):
#         # Liste des fichiers XML mais sans inclure "index.xml"
#         xml_files = [file for file in files if file.endswith(".xml") and file != "index.xml"]
#
#         # Si des fichiers XML (autres que index.xml) sont trouvés dans ce répertoire, on les ajoute au dictionnaire
#         if xml_files:
#             # On stocke le chemin du répertoire comme clé et la liste des fichiers XML comme valeur
#             xml_files_by_dir[root] = xml_files
#
#     return xml_files_by_dir
#
#
# def main():
#     # Ton répertoire de base, avec chemin absolu sous Windows
#     base_dir = r"C:\Users\augus\Desktop\Stage\stage\MyDapytains\tests\catalog\corpora\test_corpus\authorgroups"  # Utilisation du "r" pour un chemin brut
#
#     # Trouver tous les fichiers XML et organiser la structure
#     xml_files_by_dir = find_xml_files_and_structure(base_dir)
#
#     # Afficher le nombre de fichiers XML par répertoire
#     print(f"Nombre de fichiers XML par répertoire (hors 'index.xml') :")
#     for dir_path, files in xml_files_by_dir.items():
#         print(f"\nDans le répertoire {dir_path}: {len(files)} fichier(s) XML")
#         for file in files:
#             print(f"  - {file}")
#
#
# if __name__ == "__main__":
#     main()

import os

def afficher_dossiers(path):
    try:
        # Liste tout dans le dossier donné
        with os.scandir(path) as entries:
            for entry in entries:
                # Si c'est un dossier, on affiche son nom
                if entry.is_dir():
                    print(entry.name)
    except FileNotFoundError:
        print(f"Le dossier '{path}' n'existe pas.")
    except PermissionError:
        print(f"Permission refusée pour accéder au dossier '{path}'.")
    except Exception as e:
        print(f"Une erreur est survenue : {e}")

if __name__ == "__main__":
    chemin = input("Entrez le chemin du dossier : ")
    afficher_dossiers(chemin)

