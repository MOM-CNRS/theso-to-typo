#!/usr/bin/env python3
# Auteur : Gregory Bliault
# Licence : GPLv3 — voir LICENSE
"""
Exporte tous les concepts d'une branche SKOS (ex. "Typologie" du thésaurus
Ceramique_Lyon_th253.rdf) en CSV au format modele céramique (séparateur virgule).

Seuls les concepts feuilles (sans skos:narrower) sont classés automatiquement
en TYPE : code_type est rempli avec la partie du prefLabel avant le premier
espace (ex. "A.1 Pot à col tronconique..." -> "A.1").
code_categorie / code_groupe / code_serie restent vides, à remplir manuellement.

description_fr est extraite de skos:definition quand elle existe.
description_form est extraite de skos:note quand elle existe.
Toutes les autres colonnes restent vides.

Usage:
    python3 export_csv_brut_ceramique.py --rdf Ceramique_Lyon_th253.rdf --root-label "Typologie" --out export_typologie.csv
"""

import argparse
import csv
import xml.etree.ElementTree as ET
from collections import deque

NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}
RDF_RES = "{%s}resource" % NS["rdf"]

CSV_COLUMNS = [
    "code_categorie", "code_groupe", "code_serie", "code_type",
    "nom_complet_fr", "nom_complet_en", "appellation_usuelle",
    "description_fr", "description_en", "auteur_scientifique", "illustrations",
    "datation_periode", "datation_tpq", "datation_taq", "datation_commentaire",
    "production_value", "production_ateliers", "production_aire_circulation",
    "attestations_valeur", "attestations_sites_archeologiques", "attestations_corpus_lie",
    "description_form", "description_decors", "description_marques", "description_fonction",
    "caract_phys_metrologie", "caract_phys_fabrication", "caract_phys_description_pate",
    "caract_phys_couleur_pate", "caract_phys_nature_pate", "caract_phys_inclusion",
    "caract_phys_cuisson",
    "references_referentiel", "references_typologie_scientifique",
    "relations_alignements_interne", "relations_alignements_externe",
    "commentaire",
]


def load_concepts(rdf_path: str) -> dict:
    tree = ET.parse(rdf_path)
    root = tree.getroot()
    concepts = {}
    for desc in root.findall("rdf:Description", NS):
        about = desc.get("{%s}about" % NS["rdf"])
        type_el = desc.find("rdf:type", NS)
        if type_el is None or type_el.get(RDF_RES) != "http://www.w3.org/2004/02/skos/core#Concept":
            continue

        pref = desc.find('skos:prefLabel[@{http://www.w3.org/XML/1998/namespace}lang="fr"]', NS)
        if pref is None:
            pref = desc.find("skos:prefLabel", NS)
        label = pref.text.strip() if pref is not None and pref.text else None

        narrower = [e.get(RDF_RES) for e in desc.findall("skos:narrower", NS) if e.get(RDF_RES)]

        def_el = desc.find('skos:definition[@{http://www.w3.org/XML/1998/namespace}lang="fr"]', NS)
        if def_el is None:
            def_el = desc.find("skos:definition", NS)
        definition = def_el.text.strip() if def_el is not None and def_el.text else None

        note_el = desc.find('skos:note[@{http://www.w3.org/XML/1998/namespace}lang="fr"]', NS)
        if note_el is None:
            note_el = desc.find("skos:note", NS)
        note = note_el.text.strip() if note_el is not None and note_el.text else None

        concepts[about] = {
            "label": label,
            "narrower": narrower,
            "definition": definition,
            "note": note,
        }
    return concepts


def find_root_uri(concepts: dict, label: str) -> str:
    for uri, c in concepts.items():
        if c["label"] == label:
            return uri
    raise SystemExit(f"Concept racine introuvable avec le label exact: {label!r}")


def walk(concepts: dict, root_uri: str):
    """BFS -> [(uri, depth), ...], la racine exclue."""
    order = []
    visited = set()
    q = deque([(root_uri, 0)])
    while q:
        uri, depth = q.popleft()
        if uri in visited:
            continue
        visited.add(uri)
        if depth > 0:
            order.append((uri, depth))
        for child in concepts[uri]["narrower"]:
            q.append((child, depth + 1))
    return order


def code_type_from_label(label: str) -> str:
    """'A.1 Pot à col tronconique...' -> 'A.1'"""
    return label.split(" ", 1)[0].strip() if label else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rdf", required=True)
    parser.add_argument("--root-label", required=True, help='ex. "Typologie"')
    parser.add_argument("--out", default="export_ceramique_brut.csv")
    args = parser.parse_args()

    concepts = load_concepts(args.rdf)
    root_uri = find_root_uri(concepts, args.root_label)
    order = walk(concepts, root_uri)

    leaf_count = 0
    rows = []
    for uri, depth in order:
        c = concepts[uri]
        row = {col: "" for col in CSV_COLUMNS}
        row["nom_complet_fr"] = c["label"] or ""
        row["description_fr"] = c["definition"] or ""
        row["description_form"] = c["note"] or ""
        if not c["narrower"]:
            row["code_type"] = code_type_from_label(c["label"])
            leaf_count += 1
        rows.append(row)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} concepts exportés sous '{args.root_label}' -> {args.out}")
    print(f"{leaf_count} feuille(s) classée(s) en TYPE (code_type rempli automatiquement).")
    print("code_categorie / code_groupe / code_serie laissés vides, à remplir manuellement.")


if __name__ == "__main__":
    main()
