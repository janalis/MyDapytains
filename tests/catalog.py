from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

from dapitains.metadata.classes import DublinCore, Extension, Collection

TEI_NAMESPACE = {"tei": "http://www.tei-c.org/ns/1.0"}

DC_NS = "http://purl.org/dc/terms/"
EXP_NS = "exp.com"

def clean_id(text: str) -> str:
    """Return a filesystem‑safe identifier (spaces and non‑alnum => underscore)."""
    return re.sub(r"[^\w\-]", "_", text.strip())


def get_metadata_from_tei(filepath: str) -> Dict[str, Any]:
    """Extract the Dublin Core + extension metadata we need from the TEI header."""

    tree = ET.parse(filepath)
    root = tree.getroot()

    def t(xpath: str, default: Optional[str] = None) -> Optional[str]:
        el = root.find(xpath, TEI_NAMESPACE)
        return el.text.strip() if el is not None and el.text else default

    def all_text(xpath: str) -> List[str]:
        return [el.text.strip() for el in root.findall(xpath, TEI_NAMESPACE) if el.text and el.text.strip()]

    title = t(".//tei:titleStmt/tei:title[@type='main']", "Untitled")
    subtitle = t(".//tei:sourceDesc//tei:title[@type='subtitle']")
    description = subtitle if subtitle else None

    dublin_core: List[DublinCore] = [DublinCore(term="title", value=title)]
    if description:
        dublin_core.append(DublinCore(term="description", value=description))

    author = t(".//tei:titleStmt/tei:author")
    if author:
        dublin_core.append(DublinCore(term="creator", value=author))

    date = t(".//tei:sourceDesc//tei:date") or t(".//tei:editionStmt//tei:date")
    if date:
        dublin_core.append(DublinCore(term="date", value=date))

    extensions: List[Extension] = []

    def add_ext(term: str, value: Optional[str]):
        if value:
            extensions.append(Extension(term=term, value=value))

    add_ext("http://purl.org/dc/terms/publisherPlace", t(".//tei:sourceDesc//tei:pubPlace"))
    add_ext("http://purl.org/dc/terms/publisher", t(".//tei:sourceDesc//tei:publisher"))

    series_titles = all_text(".//tei:sourceDesc//tei:series/tei:title")
    series_volumes = all_text(".//tei:sourceDesc//tei:series/tei:biblScope[@unit='volume']")

    for i, s_title in enumerate(series_titles):
        vol = series_volumes[i] if i < len(series_volumes) else ""
        serie_text = s_title + (f", {vol}" if vol else "")
        extensions.append(Extension(term="serie", value=serie_text))

    series_title_only = series_titles[0] if series_titles else "Unknown Corpus"
    work_title = t(".//tei:sourceDesc//tei:title[@type='work']") or series_title_only

    identifier = os.path.splitext(os.path.basename(filepath))[0]

    return {
        "identifier": identifier,
        "title": title,
        "description": description,
        "dublin_core": dublin_core,
        "extensions": extensions,
        "resource": True,
        "filepath": filepath,
        "corpus": series_title_only,
        "author": author or "Unknown Author",
        "work": work_title or "Unknown Work",
    }

def build_resource_element(res: Dict[str, Any]) -> ET.Element:
    """Build a <resource> element from a resource metadata dict."""
    res_el = ET.Element("resource", {
        "identifier": res["identifier"],
        "filepath": os.path.normpath(os.path.join("..", "aasc", os.path.basename(res["filepath"])))
    })

    ET.SubElement(res_el, "title").text = res["title"]
    if res["description"]:
        ET.SubElement(res_el, "description").text = res["description"]

    if res.get("author"):
        ET.SubElement(res_el, "author").text = res["author"]
    if res.get("work"):
        ET.SubElement(res_el, "work").text = res["work"]

    dc_el = ET.SubElement(res_el, "dublinCore")
    for dc in res["dublin_core"]:
        el = ET.SubElement(dc_el, dc.term)
        el.set("xmlns", DC_NS)
        el.text = dc.value

    ext_el = ET.SubElement(res_el, "extensions")
    for ext in res["extensions"]:
        tag = ext.term.split("/")[-1] if ext.term != "serie" else "serie"
        ns = DC_NS if ext.term.startswith("http://purl.org/dc/terms/") else EXP_NS
        el = ET.SubElement(ext_el, tag)
        el.set("xmlns", ns)
        el.text = ext.value

    return res_el


def build_collection_element(identifier: str, title: str, description: Optional[str] = None) -> ET.Element:
    col_el = ET.Element("collection", {"identifier": identifier})
    ET.SubElement(col_el, "title").text = title
    if description:
        dc_el = ET.SubElement(col_el, "dublinCore")
        desc_el = ET.SubElement(dc_el, "description")
        desc_el.set("xmlns", DC_NS)
        desc_el.text = description
    return col_el

def main() -> None:
    tei_dir = "./aasc"
    catalog_dir = "./catalog"
    os.makedirs(catalog_dir, exist_ok=True)

    resources: List[Dict[str, Any]] = []
    for filename in sorted(os.listdir(tei_dir)):
        if not filename.endswith(".xml"):
            continue
        filepath = os.path.join(tei_dir, filename)
        metadata = get_metadata_from_tei(filepath)
        metadata["identifier"] = metadata["identifier"]
        resources.append(metadata)

    root_el = build_collection_element("https://corpus", "Catalogue des collections")
    root_members_el = ET.SubElement(root_el, "members")

    corpus_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for res in resources:
        corpus_groups[clean_id(res["corpus"])].append(res)

    for corpus_key, corpus_resources in corpus_groups.items():
        corpus_title = corpus_resources[0]["corpus"]
        corpus_id = f"https://corpus/{corpus_key}"
        corpus_el = build_collection_element(corpus_id, f"Corpus : {corpus_title}")
        corpus_members_el = ET.SubElement(corpus_el, "members")
        root_members_el.append(corpus_el)

        author_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for res in corpus_resources:
            author_groups[clean_id(res["author"] or "Unknown Author")].append(res)

        for author_key, author_resources in author_groups.items():
            author_name = author_resources[0]["author"]
            author_id = f"{corpus_id}/{author_key}"
            author_el = build_collection_element(author_id, f"Auteur : {author_name}")
            author_members_el = ET.SubElement(author_el, "members")
            corpus_members_el.append(author_el)

            work_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for res in author_resources:
                work_groups[clean_id(res["work"] or "Unknown Work")].append(res)

            for work_key, work_resources in work_groups.items():
                work_title = work_resources[0]["work"]
                work_id = f"{author_id}/{work_key}"
                work_el = build_collection_element(work_id, f"Œuvre : {work_title}")
                work_members_el = ET.SubElement(work_el, "members")
                author_members_el.append(work_el)

                for res in work_resources:
                    res_el = build_resource_element(res)
                    work_members_el.append(res_el)

    tree = ET.ElementTree(root_el)
    output_path = os.path.join(catalog_dir, "collection.xml")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"✅ Collection hiérarchique générée dans {output_path}")

if __name__ == "__main__":
    main()