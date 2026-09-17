---
title: Veille Audio
aliases:
  - Veille Audio
type: projet
status: cadrage — non implémenté
created: 2026-09-17
updated: 2026-09-17
tags:
  - ia
  - veille
  - curation
  - tts
  - typesafe
  - python
---

# Veille Audio

## Objectif

Transformer une veille technologique subie (parcourir Hacker News, Reddit, arXiv) en une veille **écoutée** : un agent collecte, trie et rédige chaque nuit une synthèse en français, destinée à devenir à terme un court épisode audio à deux voix.

Deux objectifs, dans cet ordre :

1. **Apprendre la curation** — collecter large, noter, dédupliquer, ne garder que ce qui mérite l'attention.
2. **Apprendre la chaîne texte → voix** — mais uniquement quand le livrable texte sera bon.

## Règle directrice

> La voix, c'est 10 % du projet. La curation, c'est 90 %. Si le digest est mauvais, le podcast sera mauvais.

Conséquence opérationnelle : le premier livrable est un fichier Markdown que Stéphane lit et juge en trois minutes. Le TTS n'est ajouté qu'après validation de la qualité du digest.

## Périmètre

| Phase | Contenu | Statut |
| --- | --- | --- |
| V1 | Hacker News → digest Markdown (collecte, enrichissement, triage, rédaction) | à développer |
| V2 | Reddit par RSS, arXiv, déduplication inter-jours | envisagé |
| V3 | Script à deux voix, TTS local, flux RSS podcast | envisagé |

Hors périmètre explicite : notification par mail ou calendrier, clonage de voix, application web, comptes utilisateurs, publication automatique sur une plateforme.

## Architecture cible

```text
  sources                pipeline (une commande par étape, fichiers comme interface)
  ┌──────────┐    ┌──────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐
  │ HN       │───▶│ collect  │──▶│  enrich  │──▶│   triage   │──▶│  write   │──▶ digest.md
  │ (Algolia)│    │ items.json│   │ enriched/│   │ Jev + code │   │  (LLM)   │
  └──────────┘    └──────────┘   └──────────┘   └────────────┘   └──────────┘
  (V2) Reddit / arXiv                                        (V3) script 2 voix → TTS → RSS
```

Le triage est le seul étage qui fait du **jugement** ; la rédaction est le seul étage qui produit du **texte** ; la collecte et l'enrichissement sont volontairement bêtes et vérifiables.

## Navigation

- [Workflow du pipeline](<docs/Workflow du pipeline.md>) — les cinq étapes, leurs entrées/sorties et les invariants.
- [Triage TypeSafe](<docs/Triage TypeSafe.md>) — définition de l'état, questions typées, pondérations, coûts.
- [Handoff développeur](<docs/Handoff développeur.md>) — brief exécutable pour l'agent de développement.
- [Journal de mise en œuvre](<docs/Journal de mise en œuvre.md>) — chronologie, mesures, décisions, checklist.

## Décisions

| Décision | Alternative rejetée | Raison |
| --- | --- | --- |
| Livrable texte d'abord, voix ensuite | Épisode audio directement | Un mauvais digest doit être détecté en lisant, pas en écoutant 15 minutes |
| Sortie rédigée en français depuis les sources anglaises | Traduction puis lecture | Une étape de moins, pas de perte de nuance en cascade |
| Moteur de triage = Jev (TypeSafe) | LLM génératif classique | Sortie typée sans texte généré : pas de JSON mal formé, pas de retry payant, sortie gratuite |
| Questions et pondérations dans un fichier de config | Instructions dans un prompt | La grille devient pondérable et modifiable sans réécrire de prompt |
| Étapes en CLI rejouables, fichiers comme interface | Service long-running ou base de données | Rejouer le triage sans refetch, inspecter l'état intermédiaire, comparer deux jours |
| Python | Rust | Le gain de Rust serait de quelques millisecondes sur un job nocturne ; l'écosystème (SDK TypeSafe, extraction HTML, TTS) est python-first |
| TTS local (Piper ou Kokoro) en V3 | API cloud de TTS | Aucun clonage demandé : le local suffit pour du français, sans sortie de données ni facturation |
| `tts-serve` écarté | S'en servir comme couche moteur | Sa valeur est le clonage et la multiplicité des moteurs ; sans clonage, c'est une dépendance inutile |

## Contraintes et risques

- **Sources fragiles.** Le scraping d'articles casse régulièrement (paywalls, JS). La collecte doit tolérer l'échec par item et continuer.
- **Coût maîtrisé mais dépendant du volume.** Chiffré dans [Triage TypeSafe](<docs/Triage TypeSafe.md>) ; seule l'entrée est facturée.
- **TypeSafe est une bêta fermée**, lancée le 2026-09-15, donc derrière un adaptateur remplaçable par un modèle local.
- **Vie privée.** V1 ne traite que des sources publiques. Aucune donnée personnelle (mail, calendrier) n'entre dans le pipeline.
- **Dérive de qualité non mesurée.** Sans retour d'information, le digest peut se dégrader sans être vu. À traiter en V2 par un journal des épisodes lus ou sautés.

## Questions ouvertes

- Combien d'items retenus par digest : 5, 8, 12 ?
- Déduplication : par URL et domaine suffit-il, ou faut-il un regroupement sémantique ?
- Seuil de confiance en dessous duquel un item est écarté sans discussion ?
- Faut-il conserver les commentaires HN dans l'état, et à quelle place dans le prompt ?
- Quel modèle rédige le digest, et faut-il un modèle différent du trieur ?

## Références

Faits vérifiés le **2026-09-17** :

- API Hacker News (Algolia) : `https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=N` → HTTP 200, champs `points`, `num_comments`, `title`, `url`, `created_at`.
- API Hacker News (Firebase) : `https://hacker-news.firebaseio.com/v0/topstories.json` → HTTP 200, liste d'identifiants.
- Reddit RSS : `https://www.reddit.com/r/<sub>/top/.rss?t=day` → HTTP 200, sans authentification ; champs `title`, `id`, `author`, `updated`, `content` — **ni score ni nombre de commentaires**.
- Reddit JSON (`/top.json`) : renvoie autre chose que du JSON (blocage) — authentification nécessaire.
- arXiv : `https://export.arxiv.org/api/query?search_query=cat:cs.CL&sortBy=submittedDate&sortOrder=descending&maxResults=N` → HTTP 200 en HTTPS (en HTTP simple, réponse inutilisable).
- Flux francophones testés : `https://www.lemonde.fr/rss/une.xml` → 200 ; `https://next.ink/feed/` → 200.
- TypeSafe / Jev : entrée 0,042 USD par million de tokens, **sortie gratuite** (annonce publique du 2026-09-15, lue le 2026-09-17).

## Statut

**Phase :** cadrage
**Dernière mise à jour :** 2026-09-17

Aucune ligne de code n'est écrite ni exécutée à ce stade. Les endpoints ci-dessus ont été testés manuellement le 2026-09-17 ; les coûts sont des estimations calculées, pas des relevés d'usage réel.
