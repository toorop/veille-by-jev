---
title: Journal de mise en œuvre
aliases:
  - Journal de mise en œuvre
type: journal
status: V1 en cours — collecte opérationnelle
created: 2026-09-17
updated: 2026-09-17
tags:
  - veille
  - journal
  - décisions
---

# Journal de mise en œuvre

Retour à l'index : [veille-by-jev](../README.md).

## 2026-09-17 — cadrage

Le projet naît d'un détour : la recherche portait initialement sur le clonage de voix en local. La conclusion de cette exploration a redéfini le projet plutôt que de le fermer.

### Ce qui a été écarté, et pourquoi

| Piste | Verdict | Raison |
| --- | --- | --- |
| Clonage de voix en local (LuxTTS, XTTS, Chatterbox) | abandonné pour ce projet | LuxTTS et IndexTTS ne gèrent pas le français ; Chatterbox le gère mais le clonage n'est pas nécessaire à un digest audio |
| Clonage via une API cloud | écarté | La voix et le texte sortiraient de la machine, ce qui contredit le reste du projet |
| `tts-serve` comme couche moteur | écarté pour la V1 | Sa valeur est la multiplicité des moteurs et le clonage ; sans clonage, c'est une dépendance en trop |
| NotebookLM, Podcastfy comme socle | écartés, gardés comme références | Ils transforment les sources qu'on leur donne ; ici l'agent doit décider **ce qui mérite attention** |
| Rust pour le pipeline | écarté | Gain de quelques millisecondes sur un job nocturne, au prix d'une itération plus lente |

### Ce qui a été décidé

- Le livrable de la V1 est **écrit**, pas parlé.
- Le moteur de triage est Jev (TypeSafe), derrière un adaptateur.
- La grille de notation vit dans un fichier de configuration, pas dans un prompt.
- Les étapes sont des commandes rejouables qui communiquent par fichiers.

### Ce qui a été vérifié le jour même

- API Hacker News : Algolia et Firebase répondent.
- Reddit : le RSS passe sans authentification mais sans score ni commentaires ; le endpoint JSON est bloqué.
- arXiv : répond en HTTPS.
- Deux flux francophones (Le Monde, Next) répondent.
- Tarif de Jev : entrée à 0,042 USD le million de tokens, sortie gratuite.

### Ce qui reste à vérifier

- Le coût réel par run, à mesurer au premier essai.
- La vitesse de Jev sur un lot de 200 items.
- Le taux d'échec réel de l'extraction de texte sur des articles variés.
- La qualité de la rédaction française à partir de sources anglaises, sur un vrai lot.

## 2026-09-17 — étape 1 : la collecte

Le dépôt de code vit désormais à la racine du projet, au même niveau que `docs/` : le vault Obsidian n'est plus le contenant. Le projet s'appelle **veille-by-jev**, la commande courte **`vbj`**, et le paquet Python reste `veille/`.

### Ce qui a été construit

- `vbj collect --date AAAA-MM-JJ` : fenêtre de 24 h découpée en journée civile dans le fuseau configuré, une requête Algolia, tri par points, déduplication par URL, écriture atomique de `data/<date>/items.json`.
- Rejeu sans `--force` : rien n'est refait, aucun appel réseau.
- Un échec de source est journalisé dans le fichier sans faire tomber l'étape ; un item inexploitable est écarté sans conséquence.
- 44 tests (pytest) et un linter configuré (ruff), aucun test ne touchant le réseau.

### Mesuré sur la journée du 2026-09-16

| Grandeur | Valeur |
| --- | --- |
| Stories publiées dans la fenêtre | 1 139, dont 1 000 atteignables (plafond de pagination Algolia) |
| Items bruts reçus | 1 000 |
| Candidats conservés (points ≥ 1) | 984, tous écrits |
| Requêtes HTTP / durée | 1 requête, environ 0,9 s |
| Poids de `items.json` | 423 491 octets |
| Plage de publication couverte | 2026-09-15T22:00:28Z → 2026-09-16T21:59:47Z |
| Appels de modèle, coût | 0, 0,00 USD |

### Décisions prises à cette étape

- **Journée civile**, pas 24 h glissantes : le même `--date` redonne toujours le même lot.
- **`items.json` n'est pas tronqué** : la collecte ne coûte qu'une requête, le plafond appartient à l'étape qui paie. `config/sources.toml` ne porte donc plus de « nombre d'items visés ».
- **Anglais pour le code, la CLI et la configuration ; français pour le digest seul**, puisque le dépôt est public et que le lecteur du digest est francophone.
- **Le linter et les tests entrent dans le projet**, avec leur configuration, plutôt que d'être lancés à la main.

### Ce qui a été écarté

- Tronquer `items.json` à la taille du digest : cela rendrait tout ré-étalonnage de la grille dépendant d'une recollecte.
- Fenêtre glissante de 24 h : le run de 02:00 raterait la fin de soirée HN, et deux exécutions de la même date ne donneraient pas le même lot.

## Mesures

Les hypothèses de dimensionnement sont remplacées par des relevés au fur et à mesure. Les lignes sans mesure concernent des étapes non écrites.

| Grandeur | Hypothèse | Mesuré | Date |
| --- | --- | --- | --- |
| Items collectés par nuit | 200 | 984 candidats retenus sur 1 000 reçus | 2026-09-17 |
| Requêtes HTTP de collecte | non estimé | 1 | 2026-09-17 |
| Durée de la collecte | non estimée | 0,9 s | 2026-09-17 |
| Tokens d'état par item | 1 500 | — | — |
| Coût du triage par mois | 0,38 USD | — | — |
| Coût de la rédaction par mois | non chiffré | — | — |
| Durée totale du run nocturne | non estimée | — | — |

## Checklist

- [x] `collect` opérationnel sur une journée réelle
- [ ] `enrich` opérationnel, avec cache et tolérance aux échecs
- [ ] `triage` opérationnel avec une seule question
- [ ] grille complète et coefficients dans `config/questions.toml`
- [ ] `write` opérationnel, digest en français
- [ ] premier digest lu jusqu'au bout par Stéphane
- [ ] coût réel mesuré et reporté dans [Triage TypeSafe](<Triage TypeSafe.md>)
- [ ] décision : continuer en V2 (Reddit, arXiv) ou ajuster la grille

## Journal des décisions

| Date | Décision | Motif |
| --- | --- | --- |
| 2026-09-17 | Livrable texte avant tout TTS | Un mauvais digest se détecte en lisant, pas en écoutant |
| 2026-09-17 | Triage par questions typées plutôt qu'un prompt de notation | Fiabilité du format et coût de sortie nul |
| 2026-09-17 | Python, pas Rust | Le critère est la vitesse d'itération, pas la performance |
| 2026-09-17 | Fenêtre = journée civile dans un fuseau configurable | Rejouabilité à l'identique et couverture d'une journée HN entière |
| 2026-09-17 | `items.json` conserve tous les candidats | La collecte est gratuite ; le plafond va à l'étape qui paie |
| 2026-09-17 | Code, CLI et configuration en anglais, digest en français | Dépôt public, lecteur francophone |
| 2026-09-17 | ruff et pytest dans le projet, avec leur configuration | Les conventions et les invariants cessent de dépendre de ma discipline |
