---
title: Journal de mise en œuvre
aliases:
  - Journal de mise en œuvre
type: journal
status: cadrage — non implémenté
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

## Mesures

Aucune mesure réelle à ce jour. Les valeurs de la colonne « hypothèse » servent au dimensionnement et doivent être remplacées par des relevés dès le premier run.

| Grandeur | Hypothèse | Mesuré | Date |
| --- | --- | --- | --- |
| Items collectés par nuit | 200 | — | — |
| Tokens d'état par item | 1 500 | — | — |
| Coût du triage par mois | 0,38 USD | — | — |
| Coût de la rédaction par mois | non chiffré | — | — |
| Durée totale du run nocturne | non estimée | — | — |

## Checklist

- [ ] `collect` opérationnel sur une journée réelle
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
