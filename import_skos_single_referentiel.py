#!/usr/bin/env python3
# Auteur : Grégory BLIAULT / gregory.bliault@mom.fr
# Licence : GPLv3 — voir LICENSE

"""
Import de la branche "monnaies (GRUEL, POPOVITCH 2007)" du thésaurus SKOS Bibracte
vers OpenTypo (https://opentypo.mom.fr), en respectant la hiérarchie :

    REFERENTIEL (parent=3, fixé)
      CATEGORIE
        GROUPE
          SERIE
            TYPE (et TYPE imbriqués si la branche SKOS est plus profonde)

Usage:
    python3 import_skos_to_opentypo.py --token TON_JWT --rdf Bibracte_Thesaurus_th56.rdf
    python3 import_skos_to_opentypo.py --token TON_JWT --rdf Bibracte_Thesaurus_th56.rdf --dry-run

Le script est idempotent : il sauvegarde au fur et à mesure le mapping
uri SKOS -> id OpenTypo dans un fichier JSON (mapping.json) et reprend
là où il s'était arrêté en cas de coupure / erreur.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from collections import deque
import xml.etree.ElementTree as ET

import requests

NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
    "xml": "http://www.w3.org/XML/1998/namespace",
}

RDF_RES = "{%s}resource" % NS["rdf"]
XML_LANG = "{%s}lang" % NS["xml"]

# Mapping profondeur -> code de type d'entité OpenTypo
# 0 = le concept racine lui-même (le "referentiel")
DEPTH_TO_TYPE = {
    0: "REFERENTIEL",
    1: "CATEGORIE",
    2: "GROUPE",
    3: "SERIE",
}
DEFAULT_TYPE_BEYOND = "TYPE"  # tout ce qui est plus profond que SERIE

PARENT_ID_OF_REFERENTIEL = 3  # imposé par toi

ROOT_LABEL = "monnaies (GRUEL, POPOVITCH 2007)"


def load_concepts(rdf_path: str) -> dict:
    """Parse le RDF/XML SKOS et renvoie {uri: {label, narrower:[uris], identifier}}"""
    tree = ET.parse(rdf_path)
    root = tree.getroot()
    concepts = {}
    for desc in root.findall("rdf:Description", NS):
        about = desc.get("{%s}about" % NS["rdf"])
        type_el = desc.find("rdf:type", NS)
        if type_el is None:
            continue
        if type_el.get(RDF_RES) != "http://www.w3.org/2004/02/skos/core#Concept":
            continue

        pref = desc.find('skos:prefLabel[@xml:lang="fr"]', NS)
        if pref is None:
            # fallback sur n'importe quel prefLabel si pas de version FR
            pref = desc.find("skos:prefLabel", NS)
        label = pref.text.strip() if pref is not None and pref.text else None

        narrower = [
            e.get(RDF_RES) for e in desc.findall("skos:narrower", NS) if e.get(RDF_RES)
        ]
        ident_el = desc.find("dcterms:identifier", NS)
        identifier = ident_el.text.strip() if ident_el is not None and ident_el.text else None

        concepts[about] = {
            "label": label,
            "narrower": narrower,
            "identifier": identifier,
        }
    return concepts


def find_root_uri(concepts: dict, label: str) -> str:
    for uri, c in concepts.items():
        if c["label"] == label:
            return uri
    raise SystemExit(f"Concept racine introuvable avec le label exact: {label!r}")


def build_ordered_tree(concepts: dict, root_uri: str):
    """BFS depuis la racine -> liste ordonnée [(uri, depth, parent_uri), ...]
    garantissant que chaque parent apparait avant ses enfants."""
    order = []
    visited = set()
    q = deque([(root_uri, 0, None)])
    while q:
        uri, depth, parent = q.popleft()
        if uri in visited:
            continue
        visited.add(uri)
        if uri not in concepts:
            print(f"  [!] URI référencé mais absent du graphe, ignoré: {uri}")
            continue
        order.append((uri, depth, parent))
        for child in concepts[uri]["narrower"]:
            q.append((child, depth + 1, uri))
    return order


def entity_type_for_depth(depth: int) -> str:
    return DEPTH_TO_TYPE.get(depth, DEFAULT_TYPE_BEYOND)


def make_code(identifier: str | None, uri: str) -> str:
    """Code unique pour OpenTypo. On se base sur l'identifiant du thésaurus
    (dcterms:identifier), sinon on retombe sur le dernier segment de l'ARK."""
    if identifier:
        return f"BIB{identifier}"
    return "BIB" + uri.rsplit("/", 1)[-1][:40]


def push_entity(session, base_url, token, code, label, entity_type, parent_id, statut, dry_run):
    payload = {
        "code": label,
        "entityTypeCode": entity_type,
        "labelNom": label,
        "labelLangCode": "fr",
        "statut": statut,
        "parentEntityId": parent_id,
    }
    if dry_run:
        print(f"  [DRY-RUN] POST {payload}")
        return -1  # id factice

    resp = session.post(
        f"{base_url.rstrip('/')}/api/v1/entities",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Erreur API ({resp.status_code}) pour code={code!r} label={label!r}: {resp.text}"
        )
    data = resp.json()
    # adapte ici si le champ id renvoyé par l'API a un nom différent
    return data.get("id") or data.get("entityId") or data.get("entity_id")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rdf", required=True, help="Chemin vers le fichier RDF/SKOS")
    parser.add_argument("--token", required=True, help="Jeton Bearer OpenTypo")
    parser.add_argument("--base-url", default="https://opentypo.mom.fr", help="Base URL de l'API")
    parser.add_argument("--statut", default="PUBLIQUE", help="Statut OpenTypo des entités créées")
    parser.add_argument("--root-label", default=ROOT_LABEL, help="Label exact du concept racine SKOS à importer")
    parser.add_argument("--mapping-file", default="mapping.json", help="Fichier de correspondance uri SKOS -> id OpenTypo (reprise)")
    parser.add_argument("--dry-run", action="store_true", help="N'envoie rien, affiche juste ce qui serait poussé")
    parser.add_argument("--sleep", type=float, default=0.1, help="Pause (s) entre deux appels API")
    args = parser.parse_args()

    print(f"Lecture du RDF: {args.rdf}")
    concepts = load_concepts(args.rdf)
    print(f"{len(concepts)} concepts SKOS chargés.")

    root_uri = find_root_uri(concepts, args.root_label)
    order = build_ordered_tree(concepts, root_uri)
    print(f"{len(order)} concepts dans la branche '{args.root_label}'.")

    mapping_path = Path(args.mapping_file)
    mapping = {}
    if mapping_path.exists():
        mapping = json.loads(mapping_path.read_text())
        print(f"Reprise: {len(mapping)} entités déjà créées trouvées dans {mapping_path}.")

    session = requests.Session()

    for i, (uri, depth, parent_uri) in enumerate(order, 1):
        if uri in mapping:
            continue  # déjà poussé lors d'un run précédent

        c = concepts[uri]
        label = c["label"] or "(sans titre)"
        entity_type = entity_type_for_depth(depth)
        code = make_code(c["identifier"], uri)

        if depth == 0:
            parent_id = PARENT_ID_OF_REFERENTIEL
        else:
            parent_id = mapping.get(parent_uri)
            if parent_id is None:
                print(f"  [!] Parent non trouvé pour {label!r}, on saute (sera retenté au prochain run).")
                continue

        print(f"[{i}/{len(order)}] depth={depth} type={entity_type} code={code} label={label!r} parent={parent_id}")

        try:
            new_id = push_entity(
                session, args.base_url, args.token, code, label, entity_type,
                parent_id, args.statut, args.dry_run,
            )
        except Exception as e:
            print(f"  [ERREUR] {e}")
            print("Arrêt. Relance le script avec les mêmes arguments pour reprendre où ça a échoué.")
            mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
            sys.exit(1)

        mapping[uri] = new_id
        # sauvegarde incrémentale pour pouvoir reprendre à tout moment
        mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2))

        if args.sleep:
            time.sleep(args.sleep)

    print("Terminé.")
    print(f"Mapping sauvegardé dans: {mapping_path}")


if __name__ == "__main__":
    main()
