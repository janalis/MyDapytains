import os
import json
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)

MAPPING_PATH = os.path.join(BASE_DIR, config["xpath_config_file"])

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
