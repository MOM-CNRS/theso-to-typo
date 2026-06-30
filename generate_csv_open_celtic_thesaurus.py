#!/usr/bin/env python3
# Auteur : Grégory BLIAULT / gregory.bliault@mom.fr
# Licence : GPLv3 — voir LICENSE

"""
Génère UN CSV PAR RÉFÉRENTIEL (format modele.csv) à partir du thésaurus SKOS
"Open_Celtic_Thesaurus__OCThe__th98.rdf".

IMPORTANT : ce CSV suit les règles strictes de l'importeur OpenTypo (cf. capture
fournie) : une ligne par entité, pas seulement les types feuilles.
  - code_categorie seul rempli            -> crée une CATEGORIE
  - + code_groupe (sans serie/type)       -> crée un GROUPE
  - + code_serie (sans type)              -> crée une SERIE
  - + code_type                            -> crée un TYPE (sous la série si
                                              code_serie est rempli, sinon
                                              directement sous le groupe/categorie)

Le référentiel lui-même (ex. "1 - Monnaies celtes") est supposé déjà créé via
le script d'import des entités (script précédent) : il n'apparaît pas dans ce
CSV, c'est le niveau juste en dessous qui devient "code_categorie".

Comme certaines branches ont jusqu'à 2 niveaux de "groupe" imbriqués dans le
SKOS, seul le 1er niveau de groupe a sa propre colonne (code_groupe) ; le
niveau de groupe supplémentaire éventuel, lui, est traité comme une entité
GROUPE à part entière avec sa propre ligne (donc visible dans le fichier),
mais le mapping colonne par colonne suit toujours : code_categorie (1er niveau
sous le référentiel), code_groupe (2e niveau, optionnel), code_serie (avant le
type, optionnel), code_type (la feuille).

Pour chaque colonne code_*, on reprend le même attribut que celui poussé comme
"code" via l'API OpenTypo : le skos:prefLabel complet de l'entité correspondante.

description_fr est reconstruite à partir de skos:definition (Droit) et skos:note
(Revers) quand ils existent. Toutes les autres colonnes du modèle sont laissées
vides.

illustrations : pour les codes (préfixe par lequel le prefLabel commence, ex.
"1001,01") présents dans ID_TO_URLS ci-dessous, les couples legende:url sont
joints par "||" (ex. "Avers:https://...||Revers:https://...").

Usage:
    python3 generate_csv_open_celtic_thesaurus.py --rdf Open_Celtic_Thesaurus__OCThe__th98.rdf --out-dir export_csv/
    -> un fichier export_csv/1 - Monnaies celtes.csv, export_csv/2 - Monnaies grecques.csv, etc.
"""

import argparse
import csv
import re
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

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

# id (préfixe du prefLabel par lequel le label commence) -> liste de (legende, url).
# Format final dans la colonne illustrations : "legende:url||legende:url||..."
# Laisse legende="" pour l'instant si tu veux les compléter toi-même ensuite.
ID_TO_URLS = {
    "1001,01": [
        ("Droit (1)", "https://api.nakala.fr/data/10.34847/nkl.5b07vw0v/fe9755382d3a4e3ca181fcf5ac555e81c33f00aa"),
        ("Droit (2)", "https://gallica.bnf.fr/ark:/12148/btv1b11284882j/f1.highres"),
        ("Revers (1)", "https://api.nakala.fr/data/10.34847/nkl.bc802ci2/68fcfdbca0c3ed41b4f704e8ea5bf3bfba704d73"),
        ("Revers (2)", "https://gallica.bnf.fr/ark:/12148/btv1b11284882j/f2.highres"),
    ],
    "1001,07": [
        ("Droit", "https://api.nakala.fr/data/10.34847/nkl.53e38xw9/e7572bd9e8e853bfb7aab56678da20a5dd7d147f"),
        ("Revers", "https://api.nakala.fr/data/10.34847/nkl.2e1a7kei/c48d06a01d69edfe9ca835c671d5f525588365da"),
    ],
    "1001,35": [
        ("Droit (1)", "https://api.nakala.fr/data/10.34847/nkl.abd6kgaa/231245243243338d7a39b83dfbd7190211cfe9ce"),
        ("Droit (2)", "https://api.nakala.fr/data/10.34847/nkl.abd6kgaa/231245243243338d7a39b83dfbd7190211cfe9ce"),
        ("Revers (1)", "https://api.nakala.fr/data/10.34847/nkl.a232zudq/b2647d2f83d45019f4267021cd5afff2e569fbb3"),
        ("Revers (2)", "https://api.nakala.fr/data/10.34847/nkl.96bcv0tt/e578f690647718a6f85ba40cb55fe4ef0765fda5"),
    ],
}


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
        label = pref.text.strip() if pref is not None and pref.text else None

        narrower = [e.get(RDF_RES) for e in desc.findall("skos:narrower", NS) if e.get(RDF_RES)]
        broader = [e.get(RDF_RES) for e in desc.findall("skos:broader", NS) if e.get(RDF_RES)]

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
            "broader": broader,
            "definition": definition,
            "note": note,
        }
    return concepts


def find_scheme_top_concept(rdf_path: str) -> str:
    tree = ET.parse(rdf_path)
    root = tree.getroot()
    for desc in root.findall("rdf:Description", NS):
        type_el = desc.find("rdf:type", NS)
        if type_el is not None and type_el.get(RDF_RES) == "http://www.w3.org/2004/02/skos/core#ConceptScheme":
            top = desc.find("skos:hasTopConcept", NS)
            if top is not None:
                return top.get(RDF_RES)
    raise SystemExit("ConceptScheme / hasTopConcept introuvable.")


def csv_level(uri: str, depth: int, concepts: dict) -> str:
    """Niveau CSV STRICTEMENT basé sur la profondeur (depth=0 = le référentiel,
    non exporté). Vérifié : aucune branche du thésaurus ne dépasse 4 niveaux
    sous le référentiel, donc ce mapping fixe est sans ambiguïté :
      1 -> CATEGORIE
      2 -> GROUPE
      3 -> SERIE
      4 (et au-delà, par sécurité) -> TYPE
    Un noeud sans enfant à un niveau < 4 (ex. "Groupe Bohème", feuille à
    depth2) reste classé à SON niveau réel (GROUPE), il n'est PAS requalifié
    en TYPE : ça respecte la règle de l'importeur où code_type ne peut être
    rempli sans code_groupe.
    """
    if depth >= 4:
        return "TYPE"
    return {1: "CATEGORIE", 2: "GROUPE", 3: "SERIE"}[depth]


def walk_with_depth(concepts: dict, root_uri: str):
    """BFS -> [(uri, depth), ...] pour un arbre référentiel."""
    visited = {}
    q = deque([(root_uri, 0)])
    while q:
        uri, d = q.popleft()
        if uri in visited:
            continue
        visited[uri] = d
        for n in concepts[uri]["narrower"]:
            q.append((n, d + 1))
    return visited


def description_from_skos(c: dict) -> str:
    parts = []
    if c["definition"]:
        parts.append(c["definition"])
    if c["note"]:
        parts.append(c["note"])
    return " ".join(parts)


def code_prefix(label: str) -> str:
    """'1001,01_1.01 - Visage ...' -> '1001,01' (utilisé seulement pour retrouver
    les illustrations, PAS comme code OpenTypo)."""
    return label.split("_", 1)[0].strip() if label else ""


def safe_filename(label: str) -> str:
    """Label de référentiel -> nom de fichier sûr."""
    name = label.strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name[:150]


def build_row(uri, depths, concepts):
    """Construit la ligne CSV pour le concept `uri`, en respectant les règles :
    code_categorie toujours rempli ; code_groupe/code_serie/code_type remplis
    seulement jusqu'au niveau de `uri` lui-même, le reste vide."""
    level = csv_level(uri, depths[uri], concepts)

    code_categorie = code_groupe = code_serie = code_type = ""

    # remonte la chaîne broader pour récupérer les codes des ancêtres
    cur = uri
    while concepts[cur]["broader"]:
        parent = concepts[cur]["broader"][0]
        parent_depth = depths.get(parent)
        if parent_depth is None or parent_depth == 0:
            break  # on s'arrête au référentiel (déjà créé, pas exporté)
        t = csv_level(parent, parent_depth, concepts)
        if t == "CATEGORIE":
            code_categorie = concepts[parent]["label"] or ""
            break
        elif t == "GROUPE" and not code_groupe:
            code_groupe = concepts[parent]["label"] or ""
        elif t == "SERIE" and not code_serie:
            code_serie = concepts[parent]["label"] or ""
        cur = parent

    own_label = concepts[uri]["label"] or ""
    if level == "CATEGORIE":
        code_categorie = own_label
    elif level == "GROUPE":
        code_groupe = own_label
    elif level == "SERIE":
        code_serie = own_label
    elif level == "TYPE":
        code_type = own_label

    return code_categorie, code_groupe, code_serie, code_type, level


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rdf", required=True)
    parser.add_argument("--out-dir", default="export_csv")
    args = parser.parse_args()

    concepts = load_concepts(args.rdf)
    scheme_top = find_scheme_top_concept(args.rdf)
    referentiel_roots = concepts[scheme_top]["narrower"]
    print(f"{len(referentiel_roots)} référentiels détectés.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    grand_total = {"CATEGORIE": 0, "GROUPE": 0, "SERIE": 0, "TYPE": 0}

    for root_uri in referentiel_roots:
        referentiel_label = concepts[root_uri]["label"] or "referentiel"
        depths = walk_with_depth(concepts, root_uri)

        rows = []
        level_counts = {"CATEGORIE": 0, "GROUPE": 0, "SERIE": 0, "TYPE": 0}

        # ordre = BFS (déjà garanti par walk_with_depth/dict insertion) : les parents
        # sont toujours écrits avant leurs enfants dans le fichier.
        for uri, d in depths.items():
            if d == 0:
                continue  # le référentiel lui-même n'est pas exporté ici
            c = concepts[uri]
            code_categorie, code_groupe, code_serie, code_type, level = build_row(uri, depths, concepts)
            level_counts[level] += 1

            illustrations = ""
            if level == "TYPE":
                illustration_pairs = next(
                    (pairs for ident, pairs in ID_TO_URLS.items() if c["label"] and c["label"].startswith(ident)),
                    [],
                )
                illustrations = "||".join(f"{legende}:{url}" for legende, url in illustration_pairs)

            row = {col: "" for col in CSV_COLUMNS}
            row["code_categorie"] = code_categorie
            row["code_groupe"] = code_groupe
            row["code_serie"] = code_serie
            row["code_type"] = code_type
            row["nom_complet_fr"] = c["label"] or ""
            row["description_fr"] = description_from_skos(c)
            row["illustrations"] = illustrations
            rows.append(row)

        out_path = out_dir / f"{safe_filename(referentiel_label)}.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

        for k in grand_total:
            grand_total[k] += level_counts[k]
        print(f"  - {referentiel_label!r}: {len(rows)} lignes "
              f"(CATEGORIE={level_counts['CATEGORIE']}, GROUPE={level_counts['GROUPE']}, "
              f"SERIE={level_counts['SERIE']}, TYPE={level_counts['TYPE']}) -> {out_path}")

    total = sum(grand_total.values())
    print(f"Total: {total} lignes sur {len(referentiel_roots)} fichiers "
          f"(CATEGORIE={grand_total['CATEGORIE']}, GROUPE={grand_total['GROUPE']}, "
          f"SERIE={grand_total['SERIE']}, TYPE={grand_total['TYPE']}), dans {out_dir}/")


if __name__ == "__main__":
    main()