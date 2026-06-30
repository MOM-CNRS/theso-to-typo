#!/usr/bin/env python3
# Auteur : Grégory BLIAULT / gregory.bliault@mom.fr
# Licence : GPLv3 — voir LICENSE

"""
Import d'un thésaurus SKOS vers OpenTypo (https://opentypo.mom.fr).

Le concept racine du scheme (skos:hasTopConcept, ou --root-label) n'est PAS
poussé comme entité : ce sont ses enfants directs (skos:narrower) qui
deviennent chacun un référentiel séparé (entityTypeCode=REFERENTIEL),
tous rattachés à --parent-id (3 par défaut). C'est donc une forêt de
plusieurs référentiels, pas un seul.

Deux modes de classement profondeur -> type d'entité OpenTypo, au choix via --mode,
la profondeur étant comptée à partir de chaque référentiel (qui est lui-même
profondeur 0) :

  - "fixed"  : mapping strict par profondeur :
                 0=REFERENTIEL, 1=GROUPE, 2=SERIE, >=3=TYPE

  - "auto"   : classement adaptatif (utile si la profondeur des "types" varie
               selon les branches, ex. Open Celtic Thesaurus th98) :
                 0=REFERENTIEL
                 1=GROUPE (toujours)
                 >=2: feuille (pas d'enfant) -> TYPE
                      noeud dont TOUS les enfants sont des feuilles -> SERIE
                      sinon -> GROUPE (niveau intermédiaire supplémentaire)

Le "code" poussé à OpenTypo peut être soit le prefLabel SKOS (--code-from label,
par défaut), soit construit depuis dcterms:identifier (--code-from identifier).

Usage:
    python3 import_skos_to_opentypo.py --rdf fichier.rdf --token TON_JWT --mode auto
    python3 import_skos_to_opentypo.py --rdf fichier.rdf --token TON_JWT --mode auto --dry-run

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

DEPTH_TO_TYPE_FIXED = {
    0: "REFERENTIEL",
    1: "GROUPE",
    2: "SERIE",
}
DEFAULT_TYPE_BEYOND = "TYPE"  # tout ce qui est plus profond que le dernier niveau défini

PARENT_ID_OF_REFERENTIEL = 3  # imposé : le référentiel est toujours créé sous l'entité 3


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


def find_scheme_top_concept(rdf_path: str) -> str | None:
    """Renvoie l'URI du (premier) skos:hasTopConcept du ConceptScheme, si présent."""
    tree = ET.parse(rdf_path)
    root = tree.getroot()
    for desc in root.findall("rdf:Description", NS):
        type_el = desc.find("rdf:type", NS)
        if type_el is not None and type_el.get(RDF_RES) == "http://www.w3.org/2004/02/skos/core#ConceptScheme":
            top = desc.find("skos:hasTopConcept", NS)
            if top is not None:
                return top.get(RDF_RES)
    return None


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


def entity_type_fixed(depth: int) -> str:
    return DEPTH_TO_TYPE_FIXED.get(depth, DEFAULT_TYPE_BEYOND)


def entity_type_auto(uri: str, depth: int, concepts: dict) -> str:
    """Classement adaptatif, robuste aux profondeurs irrégulières (depth=0 = le référentiel lui-même) :
      0           -> REFERENTIEL
      1           -> GROUPE (toujours)
      >=2, feuille -> TYPE
      >=2, tous les enfants sont des feuilles -> SERIE
      >=2, sinon  -> GROUPE (niveau intermédiaire supplémentaire)
    """
    if depth == 0:
        return "REFERENTIEL"
    if depth == 1:
        return "GROUPE"
    children = concepts[uri]["narrower"]
    if not children:
        return "TYPE"
    if all(not concepts[c]["narrower"] for c in children):
        return "SERIE"
    return "GROUPE"


_CODE_CACHE_SEEN = set()


def make_code(identifier: str | None, uri: str, label: str | None, code_from: str) -> str:
    """Code unique pour OpenTypo.
    - code_from == "label"      : utilise le prefLabel SKOS tel quel (demande explicite),
                                   avec désambiguïsation si jamais deux concepts avaient
                                   le même libellé exact.
    - code_from == "identifier" : BIB<dcterms:identifier>, sinon fallback sur l'ARK.
    """
    if code_from == "label":
        base = (label or uri.rsplit("/", 1)[-1]).strip()
        code = base
        suffix = 2
        while code in _CODE_CACHE_SEEN:
            code = f"{base} ({suffix})"
            suffix += 1
        _CODE_CACHE_SEEN.add(code)
        return code

    if identifier:
        return f"BIB{identifier}"
    return "BIB" + uri.rsplit("/", 1)[-1][:40]


def push_entity(session, base_url, token, code, label, entity_type, parent_id, statut, dry_run):
    payload = {
        "code": code,
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
    parser.add_argument("--root-label", default=None, help="Label exact du concept racine SKOS à importer (sinon: skos:hasTopConcept du scheme)")
    parser.add_argument("--mode", choices=["fixed", "auto"], default="auto",
                         help="fixed: mapping strict par profondeur (0=REFERENTIEL,1=CATEGORIE,2=GROUPE,3=SERIE,4+=TYPE). "
                              "auto: classement adaptatif robuste aux profondeurs irrégulières (0=REFERENTIEL,1-2=GROUPE,puis SERIE/TYPE selon les enfants). Défaut: auto.")
    parser.add_argument("--code-from", choices=["label", "identifier"], default="label",
                         help="Source du champ 'code' poussé à OpenTypo: le prefLabel SKOS (défaut) ou un code basé sur dcterms:identifier.")
    parser.add_argument("--parent-id", type=int, default=PARENT_ID_OF_REFERENTIEL,
                         help=f"parentEntityId du référentiel racine (défaut: {PARENT_ID_OF_REFERENTIEL})")
    parser.add_argument("--mapping-file", default="mapping.json", help="Fichier de correspondance uri SKOS -> id OpenTypo (reprise)")
    parser.add_argument("--dry-run", action="store_true", help="N'envoie rien, affiche juste ce qui serait poussé")
    parser.add_argument("--sleep", type=float, default=0.1, help="Pause (s) entre deux appels API")
    args = parser.parse_args()

    print(f"Lecture du RDF: {args.rdf}")
    concepts = load_concepts(args.rdf)
    print(f"{len(concepts)} concepts SKOS chargés.")

    if args.root_label:
        scheme_root_uri = find_root_uri(concepts, args.root_label)
    else:
        scheme_root_uri = find_scheme_top_concept(args.rdf)
        if scheme_root_uri is None or scheme_root_uri not in concepts:
            raise SystemExit("Impossible de déterminer le concept racine automatiquement, précise --root-label.")
        print(f"Racine détectée automatiquement (skos:hasTopConcept): {concepts[scheme_root_uri]['label']!r}")

    # Les enfants directs du concept racine deviennent chacun un référentiel séparé
    # (le concept racine lui-même n'est pas poussé comme entité).
    referentiel_roots = concepts[scheme_root_uri]["narrower"]
    if not referentiel_roots:
        raise SystemExit(f"Le concept racine {concepts[scheme_root_uri]['label']!r} n'a aucun skos:narrower : rien à importer.")
    print(f"{len(referentiel_roots)} référentiel(s) détecté(s) sous '{concepts[scheme_root_uri]['label']}':")
    for r in referentiel_roots:
        print(f"   - {concepts[r]['label']}")

    order = []
    for r in referentiel_roots:
        order.extend(build_ordered_tree(concepts, r))
    print(f"{len(order)} concepts au total à importer.")

    # petit récap du classement avant de pousser quoi que ce soit
    counts = {}
    for uri, depth, _ in order:
        t = entity_type_fixed(depth) if args.mode == "fixed" else entity_type_auto(uri, depth, concepts)
        counts[t] = counts.get(t, 0) + 1
    print(f"Répartition par type (mode={args.mode}): {counts}")

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
        entity_type = entity_type_fixed(depth) if args.mode == "fixed" else entity_type_auto(uri, depth, concepts)
        code = make_code(c["identifier"], uri, label, args.code_from)

        if depth == 0:
            parent_id = args.parent_id
        else:
            parent_id = mapping.get(parent_uri)
            if parent_id is None:
                print(f"  [!] Parent non trouvé pour {label!r}, on saute (sera retenté au prochain run).")
                continue

        print(f"[{i}/{len(order)}] depth={depth} type={entity_type} code={code!r} label={label!r} parent={parent_id}")

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
