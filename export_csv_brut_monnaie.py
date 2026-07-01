#!/usr/bin/env python3
# Auteur : Grégory BLIAULT / gregory.bliault@mom.fr
# Licence : GPLv3 — voir LICENSE
"""
Exporte TOUS les concepts d'une branche SKOS (ex. "Typologie" du thésaurus
Ceramique_Lyon_th253.rdf) en CSV (format modele.csv).

code_categorie / code_groupe / code_serie restent vides (à déterminer
manuellement). En revanche, pour les concepts FEUILLES (sans skos:narrower),
on considère que ce sont des types : code_type est rempli automatiquement
avec la partie du prefLabel avant le premier espace (ex. "A.1 Pot à col
tronconique..." -> code_type = "A.1").

Usage:
    python3 export_csv_brut.py --rdf Ceramique_Lyon_th253.rdf --root-label "Typologie" --out export_typologie.csv
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
    "description_droit", "description_legende_droit", "description_revers", "description_legende_revers",
    "caract_phys_materiau", "caract_phys_denomination", "caract_phys_metrologie",
    "caract_phys_valeur", "caract_phys_technique",
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


def walk_with_parent(concepts: dict, root_uri: str):
    """BFS -> [(uri, depth, parent_label), ...], la racine elle-même incluse (depth=0)."""
    order = []
    visited = set()
    q = deque([(root_uri, 0, None)])
    while q:
        uri, depth, parent_label = q.popleft()
        if uri in visited:
            continue
        visited.add(uri)
        order.append((uri, depth, parent_label))
        for child in concepts[uri]["narrower"]:
            q.append((child, depth + 1, concepts[uri]["label"]))
    return order


def description_from_skos(c: dict) -> str:
    parts = []
    if c["definition"]:
        parts.append(c["definition"])
    if c["note"]:
        parts.append(c["note"])
    return " ".join(parts)


def code_type_from_label(label: str) -> str:
    """'A.1 Pot à col tronconique...' -> 'A.1' (partie avant le 1er espace)."""
    return label.split(" ", 1)[0].strip() if label else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rdf", required=True)
    parser.add_argument("--root-label", required=True, help='ex. "Typologie"')
    parser.add_argument("--out", default="export_brut.csv")
    parser.add_argument("--include-root", action="store_true",
                         help="Inclut le concept racine lui-même comme première ligne (exclu par défaut).")
    args = parser.parse_args()

    concepts = load_concepts(args.rdf)
    root_uri = find_root_uri(concepts, args.root_label)
    order = walk_with_parent(concepts, root_uri)
    if not args.include_root:
        order = [o for o in order if o[1] > 0]

    rows = []
    leaf_count = 0
    for uri, depth, parent_label in order:
        c = concepts[uri]
        row = {col: "" for col in CSV_COLUMNS}
        row["nom_complet_fr"] = c["label"] or ""
        row["description_fr"] = description_from_skos(c)
        if not c["narrower"]:  # feuille -> type
            row["code_type"] = code_type_from_label(c["label"])
            leaf_count += 1
        rows.append(row)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} concepts exportés sous '{args.root_label}' -> {args.out}")
    print(f"{leaf_count} feuille(s) classée(s) en TYPE (code_type rempli automatiquement).")
    print("Colonnes code_categorie / code_groupe / code_serie laissées vides, à toi de les remplir.")


if __name__ == "__main__":
    main()
