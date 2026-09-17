---
title: Triage TypeSafe
aliases:
  - Triage TypeSafe
type: procédure
status: cadrage — non implémenté
created: 2026-09-17
updated: 2026-09-17
tags:
  - veille
  - triage
  - typesafe
  - jev
  - curation
---

# Triage TypeSafe

## Objectif

Décrire l'étage de jugement du pipeline : ce qu'on envoie (l'état), ce qu'on demande (les questions), et comment les réponses sont combinées.

Retour à l'index : [Veille Audio](../README.md) · étape amont : [Workflow du pipeline](<Workflow du pipeline.md>).

## Pourquoi un modèle de décision plutôt qu'un LLM

Jev (TypeSafe) ne génère pas de texte : il évalue des questions typées contre un état et renvoie des valeurs structurées avec une distribution de probabilité et un indice de confiance. Trois conséquences directes pour ce projet :

1. **Pas de JSON mal formé.** La réponse est typée par construction : plus de parsing, plus de retry payant sur une sortie invalide.
2. **Pas de texte de raisonnement facturé.** Or c'est précisément ce qu'un LLM génératif coûte le plus cher sur une tâche de notation.
3. **Évaluation parallèle et isolée.** Les questions sur un même item sont évaluées indépendamment les unes des autres : ajouter une question ne dégrade pas les autres réponses. C'est ce qui permet une grille fine plutôt qu'un score unique et flou.

Le modèle est un **trieur, pas un lecteur** : il ne rédige rien. La rédaction du digest reste le travail d'un LLM génératif, sur un nombre d'items réduit par le triage.

## Définition de l'état

Ce que le trieur reçoit pour un item — à garder court, puisque seule l'entrée est facturée :

- titre ;
- source et catégorie (section HN, subreddit le cas échéant) ;
- score de la source et nombre de commentaires ;
- âge de la publication ;
- type d'URL : source primaire (papier, annonce officielle, dépôt de code), article de presse, billet de blog, discussion ;
- texte principal tronqué à environ 1 200 tokens ;
- extraits de commentaires HN retenus (V2 côté réglage).

## Les trois primitives, appliquées à ce projet

| Primitive | Question | Réponse exploitable |
| --- | --- | --- |
| `Score` | Intérêt pour un développeur francophone qui suit l'IA appliquée et l'outillage local, sur une échelle descriptive à cinq niveaux | score + distribution + confiance |
| `Score` | Densité technique : du papier de recherche à l'annonce purement marketing | score + confiance |
| `Choice` | Catégorie : recherche, outillage, matériel, industrie, société | option choisie + probabilité par option |
| `Noul` | S'agit-il d'une source primaire (papier, annonce officielle, code) ? | valeur entre 0 et 1 |

Ces quatre entrées sont **des exemples de forme**, pas la grille définitive : l'échelle descriptive, les seuils et les pondérations restent à écrire et à itérer. La grille se règle dans `config/questions.toml`, jamais dans le code.

## Combinaison dans le code

Les réponses sont agrégées par une formule explicite, avec des coefficients lisibles et modifiables :

```python
def score_item(reponses: dict) -> float:
    """Agrège les réponses typées d'un item en un score unique pondéré."""
    interet = reponses["interet"]["score"]
    densite = reponses["densite"]["score"]
    primaire = reponses["source_primaire"]["noul"]
    return (0.5 * interet) + (0.3 * densite) + (0.2 * primaire)
```

Deux règles attachées à cette agrégation :

- **La confiance filtre avant de classer.** Un item dont la confiance passe sous le seuil configuré est écarté sans discussion, même si son score est bon.
- **Le classement est journalisé**, pas seulement le résultat : on garde le score de chaque item pour pouvoir contester la grille après coup.

## Coûts

Hypothèses de dimensionnement (à confirmer par la mesure lors du premier run réel) : **200 items par nuit**, **1 500 tokens d'état par item**.

| Poste | Volume | Coût |
| --- | --- | --- |
| État total envoyé | 0,30 million de tokens par nuit, 9,0 millions par mois | — |
| Triage Jev | entrée à 0,042 USD le million de tokens, sortie gratuite | **0,38 USD par mois** |
| Même volume avec un LLM à 1 USD le million en entrée | + sortie estimée à 2,40 millions de tokens par mois | environ 11 USD par mois |
| Même volume avec un LLM à 3 USD le million en entrée | idem | environ 34 USD par mois |
| Hypothèse pessimiste : les 4 questions reconsomment chacune l'état | 36 millions de tokens par mois | 1,51 USD par mois |

Lecture honnête de ce tableau : le gain est réel (facteur 30 sur le mois) mais **les montants absolus sont négligeables dans les deux cas**. Le choix de Jev ne se justifie donc pas par l'argent, mais par la fiabilité du format, l'absence de retry payant et la parallélisation sans dégradation.

Le gain économique réel vient d'ailleurs : le triage réduit 200 items à 8, donc le LLM génératif n'est jamais payé sur 200 textes complets. C'est ce ratio de filtrage qui porte l'économie, pas le prix unitaire.

## Limites et points de vigilance

- **Bêta fermée**, acteur récent : l'appel doit passer par un adaptateur (`clients/typesafe.py`) remplaçable par un petit modèle local, sans toucher au reste du pipeline.
- **Vitesse annoncée non vérifiée** : l'affirmation « jusqu'à 200 fois plus rapide » vient de la communication de l'éditeur, pas d'une mesure reproduite.
- **Un modèle local de triage est gratuit à la marge** sur la machine cible. Face à lui, Jev n'est pas moins cher en argent, mais il l'est en temps, en calibration de confiance et en garantie de format — et il laisse le GPU libre pour le TTS de la V3.
- **Ordre de grandeur** : les volumes ci-dessus sont des hypothèses de dimensionnement. Le premier run réel doit les remplacer par des mesures, et la note doit être mise à jour en conséquence.
