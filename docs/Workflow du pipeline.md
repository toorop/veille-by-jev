---
title: Workflow du pipeline
aliases:
  - Workflow du pipeline
type: procédure
status: cadrage — non implémenté
created: 2026-09-17
updated: 2026-09-17
tags:
  - veille
  - pipeline
  - cli
  - python
---

# Workflow du pipeline

## Objectif

Décrire les cinq étapes du pipeline, leurs entrées et sorties, et les invariants qui rendent le tout rejouable et débogable.

Retour à l'index : [veille-by-jev](../README.md).

## Principe d'architecture

Chaque étape est une **commande autonome** qui lit des fichiers et en écrit d'autres. Aucun service, aucun démon, aucun état en mémoire entre deux exécutions.

Trois raisons :

1. Rejouer le triage après avoir modifié la grille de notation, sans refaire la collecte.
2. Inspecter l'état intermédiaire quand le résultat final est mauvais.
3. Comparer deux journées, ou deux versions de la grille, en diffant des fichiers.

## Les cinq étapes

### 1. Collecte — `vbj collect --date AAAA-MM-JJ`

- Entrée : configuration des sources.
- Sortie : `data/<date>/items.json` — un enregistrement par item : identifiant stable, source, URL, titre, score de la source, nombre de commentaires, horodatage de publication.
- Aucun appel à un modèle. Étape volontairement bête.
- Pour Hacker News : `https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=N` (vérifié 2026-09-17). Le tri par points sur une fenêtre glissante de 24 h est préférable au seul `front_page`, sinon la collecte dépend de l'heure d'exécution.
- Échec d'une source : journalisé, l'étape continue.

### 2. Enrichissement — `vbj enrich --date AAAA-MM-JJ`

- Entrée : `items.json`.
- Sortie : `data/<date>/enriched/<hash>.json` — texte principal extrait puis tronqué, plus les commentaires HN retenus.
- Le texte de l'article est extrait proprement (contenu principal, pas le menu ni les cookies), puis coupé à environ 1 200 tokens : c'est cette troncature qui détermine le coût de l'étape suivante, puisque seule l'entrée est facturée.
- Les premiers commentaires HN sont conservés : sur ce site, la valeur est souvent dans le fil (critique argumentée, contexte manquant) plutôt que dans le lien.
- **Cache obligatoire**, indexé par empreinte de l'URL. Un article déjà enrichi n'est jamais retéléchargé.
- Un item dont l'article est inaccessible (paywall, erreur) reste dans le lot avec un état « texte indisponible » : le triage peut alors juger sur le titre et les métadonnées seules.

### 3. Triage — `vbj triage --date AAAA-MM-JJ`

- Entrée : `items.json` + `enriched/` + `config/questions.toml`.
- Sortie : `data/<date>/scores.json` — pour chaque item, la réponse typée à chaque question, avec sa distribution et sa confiance, puis le score agrégé calculé dans le code.
- Détail complet dans [Triage TypeSafe](<Triage TypeSafe.md>).

### 4. Rédaction — `vbj write --date AAAA-MM-JJ`

- Entrée : les N items retenus par le triage, avec leur texte complet.
- Sortie : `digest/<date>.md` — c'est **le livrable**.
- Format attendu : pour chaque item retenu, le titre, la source, le lien, deux à quatre phrases de synthèse en français, et une ligne « pourquoi celui-là ». En fin de note, la liste des items écartés avec leur score, pour garder la trace de ce qui a été rejeté et pouvoir contester le tri.
- Rédaction **directement en français** à partir des sources anglaises, sans étape de traduction.

### 5. Suite (V3, hors V1)

Script à deux voix → TTS local → flux RSS podcast. Le digest Markdown est la seule entrée de cette étape : rien du pipeline précédent ne doit être modifié pour l'ajouter.

## Arborescence

```text
veille-by-jev/                 # racine du dépôt
  README.md
  docs/                       # documentation de cadrage
  pyproject.toml
  config/
    sources.toml          # sources activées et leurs paramètres
    questions.toml        # état, questions, pondérations, seuils
  veille/
    cli.py                # sous-commandes collect / enrich / triage / write
    sources/hn.py         # collecte HN
    enrich.py             # récupération, extraction, troncature, cache
    triage.py             # appel Jev, combinaison des scores
    write.py              # rédaction du digest
    store.py              # arborescence data/, empreintes, seen.jsonl
    clients/typesafe.py   # adaptateur Jev (remplaçable)
  data/
    <date>/items.json
    <date>/enriched/<hash>.json
    <date>/scores.json
  digest/
    <date>.md
  seen.jsonl              # URLs déjà digérées, append-only
```

Aucune base de données en V1. Le suivi du « déjà vu » tient dans un `seen.jsonl` en ajout seul : lisible, grepable, diffable.

## Invariants

- **Idempotence.** Rejouer une étape sans option `--force` ne réécrit pas un fichier existant et ne redépense pas d'appels facturés.
- **Reprise par date.** Toutes les étapes acceptent une date ; on peut rejouer la nuit du 12 sans toucher au reste.
- **Échec partiel toléré.** Un item en erreur ne fait pas tomber l'étape.
- **Coût mesuré.** Chaque exécution de `triage` et `write` affiche en fin de course le nombre de tokens d'entrée consommés et le coût estimé. Un pipeline qu'on ne peut pas chiffrer devient incontrôlable.
- **Traçabilité du rejet.** Ce qui a été écarté et pourquoi reste écrit quelque part ; sans cela, impossible de distinguer une grille trop stricte d'une nuit pauvre en actualité.

## Vérification

À l'issue de V1, les commandes suivantes doivent fonctionner sans intervention et sans erreur :

```bash
uv run vbj collect --date 2026-09-20
uv run vbj enrich  --date 2026-09-20
uv run vbj triage  --date 2026-09-20
uv run vbj write   --date 2026-09-20
```

Critère d'acceptation : la quatrième produit un `digest/2026-09-20.md` que Stéphane lit jusqu'au bout. C'est le seul juge qui compte à ce stade.
