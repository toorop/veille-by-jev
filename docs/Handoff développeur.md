---
title: Handoff développeur
aliases:
  - Handoff développeur
type: procédure
status: V1 en cours — étape 1 faite (collecte)
created: 2026-09-17
updated: 2026-09-17
tags:
  - veille
  - handoff
  - python
  - cli
  - typesafe
---

# Handoff développeur

## But de ce document

Brief autonome et exécutable pour implémenter la V1 de [veille-by-jev](../README.md). Tout ce qui est nécessaire est explicité ici : aucun élément ne dépend d'une conversation antérieure.

Contexte de développement : Stéphane développe en rédigeant d'abord un document de cadrage, puis en avançant par petites étapes testées et validées. Les étapes doivent être livrables et vérifiables une par une, pas par un gros lot final.

## Objectif de la V1

Un CLI Python, exécutable sur la machine de développement, qui produit chaque nuit un fichier `digest/<date>.md` en français à partir des articles de Hacker News de la journée.

**Hors périmètre V1** : Reddit, arXiv, synthèse audio, TTS, flux RSS, interface web, base de données, notifications, déploiement.

## Environnement

- Linux, Python 3.11 ou plus récent, gestion des dépendances et de l'environnement virtuel avec `uv`.
- Machine : GPU NVIDIA RTX 5080, 64 Go de RAM, processeur Ryzen 9800X3D. Le GPU n'est pas utilisé en V1.
- Accès à l'API TypeSafe (bêta) : clé fournie par Stéphane via variable d'environnement. La clé ne doit jamais être écrite dans un fichier du dépôt.
- Le dépôt de code vit à la racine du projet, au même niveau que `docs/`. Seul `docs/` contient de la documentation : le reste est du code et des données générées.

## Arborescence attendue

```text
veille-by-jev/                 # racine du dépôt (= dossier de travail)
  README.md                   # index du projet
  docs/                       # documentation de cadrage (ce dossier-ci)
  pyproject.toml
  .gitignore
  .env.example                # noms de variables, jamais de valeurs
  config/
    sources.toml
    questions.toml
  veille/
    cli.py
    config.py                   # lecture des TOML et calcul de la fenêtre
    models.py                   # contrat entre étapes (Item, Window, déduplication)
    store.py
    sources/hn.py
    enrich.py
    triage.py
    write.py
    clients/typesafe.py
  tests/                        # pytest, aucun test ne touche le réseau
  data/<date>/{items.json,enriched/,scores.json}
  digest/<date>.md
  seen.jsonl
```

## Sous-commandes à livrer

| Commande | Entrée | Sortie | Contrainte |
| --- | --- | --- | --- |
| `vbj collect --date J` | `config/sources.toml` | `data/J/items.json` | aucun appel de modèle |
| `vbj enrich --date J` | `items.json` | `data/J/enriched/<hash>.json` | cache par empreinte d'URL |
| `vbj triage --date J` | `items.json` + `enriched/` + `config/questions.toml` | `data/J/scores.json` | appelle TypeSafe |
| `vbj write --date J` | les N premiers de `scores.json` | `digest/J.md` | appelle un LLM génératif |

Détail des étapes et des invariants : [Workflow du pipeline](<Workflow du pipeline.md>). Détail de l'état, des questions et des coûts : [Triage TypeSafe](<Triage TypeSafe.md>).

## Dépendances

- Client HTTP : `httpx`.
- Extraction du texte principal d'une page : `trafilatura`.
- Lecture RSS (V2, mais préparer l'abstraction) : `feedparser`.
- Validation des configurations et des réponses : `pydantic`.
- CLI : `typer` ou `argparse` — au choix de l'implémenteur, l'essentiel étant que chaque sous-commande soit documentée par `--help`.
- SDK TypeSafe officiel s'il est publié ; sinon appel HTTP direct encapsulé dans `clients/typesafe.py`.
- Rédaction : un LLM génératif, appelé via une interface également encapsulée.
- Développement seulement : `pytest` pour les tests, `ruff` pour le format et le lint, tous deux dans le groupe `dev` et configurés dans `pyproject.toml`.

## Configurations

`config/sources.toml` — sources activées, fenêtre temporelle, plancher de score. **Aucun plafond d'items ici** : la collecte ne coûte qu'une requête et conserve tous les candidats, donc le plafond vit là où il est facturé, c'est-à-dire dans `enrich`, puis dans `triage`.

`config/questions.toml` — définition de l'état, questions typées, échelle descriptive de chaque `Score`, seuil de confiance, coefficients de la formule d'agrégation. **C'est le fichier que Stéphane modifiera pour itérer sur la qualité du tri.** Il doit être lisible et commenté, et un changement de ce fichier ne doit jamais demander de toucher au code.

## Contrat de sortie du digest

`digest/<date>.md`, en Markdown :

- un titre daté ;
- pour chaque item retenu : titre original, source, lien, deux à quatre phrases de synthèse **en français**, et une ligne « pourquoi celui-là » ;
- en fin de note, une section des items écartés avec leur score, pour rendre le tri contestable ;
- en fin de note, le coût du run : nombre de tokens d'entrée et coût estimé.

Ce fichier est la seule entrée prévue de la V3 audio : sa structure doit rester stable.

## Définition de la V1 terminée

- Les quatre commandes s'enchaînent sans intervention manuelle pour une date donnée.
- Rejouer `collect` ou `enrich` ne réexécute pas un travail déjà fait et ne reconsomme pas de crédit.
- Un item dont l'article est inaccessible ne fait pas échouer le run.
- Le digest est produit en français, avec les liens et les sources, et Stéphane le lit jusqu'au bout : c'est le seul critère de succès qui compte à ce stade.
- Chaque commande affiche à la fin le coût qu'elle a généré.

## Ordre de travail conseillé

**Règle d'exécution : s'arrêter à la fin de chaque étape numérotée**, montrer la sortie réelle de la commande et attendre la validation de Stéphane avant de passer à la suivante. Ne jamais enchaîner les six étapes d'un seul lot.

1. `collect` seul, jusqu'à obtenir un `items.json` correct sur une journée réelle.
2. `enrich` avec cache, en vérifiant qu'un texte de 1 200 tokens environ est bien extrait.
3. `triage` avec **une seule** question, pour valider le contrat d'appel et le format de réponse.
4. Ajout des autres questions et de l'agrégation pondérée.
5. `write` et première lecture réelle du digest.
6. Seulement ensuite : réglage de la grille, ajout de sources.

## Pièges connus

- **Hacker News.** L'endpoint `front_page` seul rend la collecte dépendante de l'heure d'exécution ; préférer un tri par points sur une fenêtre glissante de 24 h. Endpoints vérifiés le 2026-09-17 : `hn.algolia.com/api/v1/search` et `hacker-news.firebaseio.com/v0/topstories.json`.
- **Reddit** (V2 seulement). Le RSS `reddit.com/r/<sub>/top/.rss?t=day` fonctionne sans authentification mais ne fournit **ni score ni nombre de commentaires** ; le endpoint `.json` est bloqué. Ne pas bâtir de tri sur un signal absent.
- **arXiv** (V2). Utiliser HTTPS : en HTTP simple, la réponse est inutilisable.
- **Extraction de texte.** Prévoir l'échec : paywalls, pages JS. Un item sans texte doit rester triable sur ses métadonnées.
- **Coût.** Seule l'entrée est facturée par le trieur : la troncature de l'état est le principal levier de coût. Ne pas envoyer d'articles entiers.
- **Secrets.** Clé d'API en variable d'environnement uniquement, `.env` ignoré par git, `.env.example` sans valeur.
- **Ce qui n'est pas mesuré ne doit pas être affirmé.** Les volumes et coûts de [Triage TypeSafe](<Triage TypeSafe.md>) sont des hypothèses de dimensionnement : le premier run doit les remplacer par des mesures réelles.
