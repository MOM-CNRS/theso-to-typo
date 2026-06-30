# theso-to-typo

Scripts Python pour importer des thésaurus SKOS (RDF/XML) vers [OpenTypo](https://opentypo.mom.fr), sous forme d'arborescence d'entités (REFERENTIEL > CATEGORIE/GROUPE > SERIE > TYPE), via l'API ou via des CSV d'import en masse.

Ces scripts ont été écrits au fil de l'eau pour traiter plusieurs thésaurus réels (Bibracte, Open Celtic Thesaurus, Céramique Lyon). Les hiérarchies SKOS ne sont pas toujours régulières (profondeur variable selon les branches), donc les scripts proposent plusieurs stratégies de classement configurables plutôt qu'un mapping figé.

## Avertissement

⚠️ Ces scripts sont des expérimentations réalisées lors de la journée de test d'OpenTypo le 30 juin 2026 à Lyon. Je ne garantis pas qu'ils exportent ou importent correctement l'intégralité des thésaurus vers OpenTypo : les hiérarchies SKOS réelles sont parfois irrégulières, et le mapping vers les niveaux OpenTypo (REFERENTIEL/CATEGORIE/GROUPE/SERIE/TYPE) repose sur des heuristiques qui peuvent ne pas correspondre à tous les cas. Vérifie toujours les résultats (--dry-run, relecture des CSV générés) avant tout import en production. Utilisation à tes propres risques.

## Sommaire des scripts

| Script | Rôle |
|---|---|
| `import_skos_single_referentiel.py` | Import via l'API OpenTypo (`POST /api/v1/entities`) d'**un seul** référentiel racine, avec mapping fixe par profondeur (`REFERENTIEL > CATEGORIE > GROUPE > SERIE > TYPE`). Idempotent (reprise via `mapping.json`). |
| `import_skos_forest.py` | Version généralisée du précédent : gère une **forêt** de plusieurs référentiels (les enfants directs du concept racine du scheme deviennent chacun un référentiel séparé), avec un mode de classement `fixed` (profondeur stricte) ou `auto` (adaptatif, robuste aux branches irrégulières). |
| `generate_csv_open_celtic_thesaurus.py` | Génère, à partir d'un thésaurus SKOS, un CSV d'import en masse par référentiel (format compatible avec l'importeur CSV d'OpenTypo : `code_categorie` / `code_groupe` / `code_serie` / `code_type`), avec gestion des illustrations (URL + légende). Hiérarchie fixe sur 4 niveaux, vérifiée pour Open Celtic Thesaurus. |
| `export_csv_brut.py` | Exporte tous les concepts d'une branche SKOS en CSV (format modele.csv) sans présupposer toute la hiérarchie : seuls les concepts feuilles sont classés en TYPE (code_type = partie du label avant le 1er espace), les colonnes `code_categorie`/`code_groupe`/`code_serie` restent vides pour un tri manuel ultérieur. |

## Pré-requis

```bash
pip install requests
```

(`generate_csv_open_celtic_thesaurus.py` et `export_csv_brut.py` n'utilisent que la bibliothèque standard.)

## 1. Import API — `import_skos_single_referentiel.py` (référentiel unique)

Pousse l'arborescence complète d'**un seul** concept racine SKOS vers OpenTypo, avec le mapping fixe suivant :

```
profondeur 0 -> REFERENTIEL (parent = entité 3, fixé)
profondeur 1 -> CATEGORIE
profondeur 2 -> GROUPE
profondeur 3 -> SERIE
profondeur 4+ -> TYPE
```

```bash
python3 import_skos_single_referentiel.py \
  --rdf Bibracte_Thesaurus_th56.rdf \
  --token TON_JWT \
  --root-label "monnaies (GRUEL, POPOVITCH 2007)" \
  --dry-run
```

Retire `--dry-run` une fois vérifié.

### Options principales

- `--rdf` (obligatoire) : chemin du fichier RDF/SKOS
- `--token` (obligatoire) : jeton Bearer OpenTypo
- `--root-label` : label exact du concept racine à importer
- `--base-url` : URL de base de l'API (défaut `https://opentypo.mom.fr`)
- `--statut` : statut OpenTypo des entités créées (défaut `PUBLIQUE`)
- `--mapping-file` : fichier de correspondance `uri SKOS -> id OpenTypo` (défaut `mapping.json`), permet de relancer le script sans tout recréer en cas d'interruption
- `--sleep` : pause entre deux appels API (défaut `0.1`s)

## 2. Import API — `import_skos_forest.py` (forêt de référentiels)

Version recommandée pour les thésaurus dont le concept racine du scheme regroupe **plusieurs** référentiels distincts (ex. "1 - Monnaies celtes", "2 - Monnaies grecques", ... sous "Open Celtic Thesaurus"). Le concept racine du scheme lui-même n'est pas importé : ce sont ses enfants directs (`skos:narrower`) qui deviennent chacun un référentiel séparé, tous rattachés à `--parent-id`.

```bash
python3 import_skos_forest.py \
  --rdf Open_Celtic_Thesaurus__OCThe__th98.rdf \
  --token TON_JWT \
  --mode auto \
  --code-from label \
  --dry-run
```

### Modes de classement (`--mode`)

- `fixed` : mapping strict par profondeur — `0=REFERENTIEL, 1=GROUPE, 2=SERIE, 3+=TYPE`. À utiliser si le thésaurus est régulier.
- `auto` (défaut) : classement adaptatif, robuste si la profondeur des "types" varie selon les branches :
  - `0` → REFERENTIEL
  - `1` → GROUPE (toujours)
  - `≥2`, feuille → TYPE
  - `≥2`, tous les enfants sont des feuilles → SERIE
  - `≥2`, sinon → GROUPE (niveau intermédiaire supplémentaire)

### Options principales

En plus de celles de `import_skos_single_referentiel.py` :

- `--root-label` : si absent, la racine est détectée automatiquement via `skos:hasTopConcept` du `ConceptScheme`
- `--code-from {label,identifier}` : source du champ `code` poussé à OpenTypo — le `prefLabel` SKOS (défaut) ou un code basé sur `dcterms:identifier`
- `--parent-id` : `parentEntityId` des référentiels racines (défaut `3`)

## 3. Génération CSV en masse — `generate_csv_open_celtic_thesaurus.py`

Génère **un CSV par référentiel** au format attendu par l'importeur CSV d'OpenTypo, en respectant ses règles de remplissage :

| Colonne | Règle |
|---|---|
| `code_categorie` | Obligatoire. Seul renseigné → import d'une CATEGORIE. |
| `code_groupe` | Optionnel, avec `code_categorie` → import d'un GROUPE. |
| `code_serie` | Optionnel, avec `code_groupe` → import d'une SERIE. |
| `code_type` | Optionnel, avec `code_groupe` → import d'un TYPE (sous la série si `code_serie` est aussi renseigné, sinon directement sous le groupe). |

Le mapping profondeur → niveau est **fixe** (vérifié : aucune branche du thésaurus traité ne dépasse 4 niveaux sous le référentiel) :

```
profondeur 1 -> CATEGORIE
profondeur 2 -> GROUPE
profondeur 3 -> SERIE
profondeur 4 -> TYPE
```

Une ligne est générée par entité à chaque niveau (pas seulement les types), parents toujours écrits avant leurs enfants. Le référentiel lui-même (créé via `import_skos_single_referentiel.py`/`import_skos_forest.py`) n'est pas exporté dans ce CSV.

```bash
python3 generate_csv_open_celtic_thesaurus.py \
  --rdf Open_Celtic_Thesaurus__OCThe__th98.rdf \
  --out-dir export_csv/
```

→ génère `export_csv/1 - Monnaies celtes.csv`, `export_csv/2 - Monnaies grecques.csv`, etc.

### Description et illustrations

- `description_fr` est reconstruite depuis `skos:definition` (description du droit) et `skos:note` (description du revers) quand ils existent.
- `illustrations` : associe les codes (préfixe par lequel le `prefLabel` commence) à une liste de couples `(légende, url)` via le dictionnaire `ID_TO_URLS` en tête de script. Format final dans le CSV : `legende:url||legende:url||...`. Édite cette table directement dans le script selon tes besoins.

Toutes les autres colonnes du modèle (datation, production, attestations, caractéristiques physiques, références, relations, commentaire) sont laissées vides.

## 4. Export brut pour tri manuel — `export_csv_brut.py`

À utiliser quand la hiérarchie d'une branche SKOS n'est pas encore claire / décidée (ex. branche "Typologie" du thésaurus Céramique Lyon). Exporte tous les concepts de la branche en CSV (format modele.csv), sans présupposer categorie/groupe/serie :

- les concepts **feuilles** (sans `skos:narrower`) sont considérés comme des TYPE : `code_type` est rempli automatiquement avec la partie du `prefLabel` avant le premier espace (ex. `"A.1 Pot à col tronconique..."` → `code_type = "A.1"`)
- `code_categorie` / `code_groupe` / `code_serie` restent vides, à remplir manuellement après relecture
- `nom_complet_fr` et `description_fr` (depuis `skos:definition`/`skos:note`) sont déjà renseignés

```bash
python3 export_csv_brut.py \
  --rdf Ceramique_Lyon_th253.rdf \
  --root-label "Typologie" \
  --out export_typologie.csv
```

## Limites connues / points d'attention

- Les hiérarchies SKOS réelles ne sont pas toujours homogènes en profondeur : vérifie toujours en `--dry-run` (scripts API) ou en relisant la répartition par niveau affichée en console (script CSV) avant de pousser des données en masse.
- Le nom du champ `id` renvoyé par l'API OpenTypo dans `push_entity()` (`id` / `entityId` / `entity_id`) est géré en fallback ; adapte si l'API renvoie un autre nom de champ.
- `import_skos_single_referentiel.py` et `import_skos_forest.py` sont idempotents via leur fichier de mapping JSON, mais ce mapping est local à la machine : pas de vérification d'existence côté serveur en cas de ré-exécution depuis une autre machine.
- Certaines définitions SKOS contiennent du HTML brut (balises `<p>`, `<strong>`, etc.) repris tel quel dans `description_fr` par `export_csv_brut.py` : à nettoyer si besoin avant import.

## Auteur

Gregory Bliault


## Licence

Ce projet est distribué sous licence **GNU General Public License v3.0 (GPLv3)**. Voir le fichier [`LICENSE`](./LICENSE).

En résumé : tu es libre d'utiliser, modifier et redistribuer ce code, à condition que toute version modifiée distribuée reste sous la même licence et que son code source soit rendu disponible.
