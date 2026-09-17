# Testing a writer outside the pipeline

Note opérationnelle pour comparer des modèles de rédaction sans passer par `vbj write`, par
exemple dans une application qui envoie le même prompt système et le même prompt utilisateur à
plusieurs modèles.

## Les deux morceaux à récupérer

| Morceau | Où | Versionné ? |
| --- | --- | --- |
| Prompt système | `config/write-prompt.md` | oui, c'est un fichier de configuration |
| Prompt utilisateur | produit par `vbj write --dump-prompt` | non, voir ci-dessous |

Le prompt utilisateur n'est **pas** versionné, et c'est volontaire : il contient le texte intégral
des articles et les commentaires Hacker News, donc du contenu de tiers. Il vit à côté des données
dont il vient, dans `data/<date>/`, que git ignore. C'est la même raison qui garde `data/` et
`digest/` hors du dépôt public.

## Le produire

```bash
uv run vbj write --date 2026-09-16 --dump-prompt data/2026-09-16/write-user-prompt.json
```

Cette commande construit l'état exact que `write` enverrait, l'écrit, et s'arrête : **aucune clé
n'est lue, aucune requête n'est faite, rien n'est facturé**. Elle fonctionne même sans clé
OpenRouter, et même si le digest du jour existe déjà.

Le fichier de référence du 2026-09-16 est en place : 63 103 caractères, 8 items.

## Ce que contient le prompt utilisateur

Un seul objet JSON, deux clés :

```json
{ "date": "2026-09-16", "items": [ { … }, … ] }
```

Chaque item porte :

| Champ | Contenu |
| --- | --- |
| `id` | l'identifiant stable, `hn:<objectID>` |
| `title` | le titre original, en anglais |
| `url` | le lien de l'article |
| `source_score`, `source_comments` | les points et le nombre de commentaires HN |
| `aggregate` | le score agrégé du triage, sur l'échelle 0–4 |
| `answers` | la valeur brute de chaque question pondérée (`interest`, `density`, `primary_source`) |
| `article` | `status`, `truncated`, `text`, `reason` — le texte extrait, tronqué à 2 000 tokens estimés, ou `unavailable` si l'extraction a échoué |
| `thread` | jusqu'à 5 commentaires HN, `author` et `text`, absent s'il n'y en a pas |

Le prompt système demande au modèle de **ne jamais inventer** un fait, un chiffre ou un nom
absent de cet état, et de dire explicitement quand l'article n'a pas pu être lu.

## Ce que le modèle doit rendre

JSON seul, sans texte autour ni balises de code :

```json
{"items": [{"id": "hn:49731360",
            "synthese": "deux à quatre phrases en français",
            "pourquoi": "une phrase en français"}]}
```

Autant d'entrées que d'items reçus, dans le même ordre, avec l'`id` recopié à l'identique.

**Le reste du digest n'est pas demandé au modèle.** Le titre daté, les liens, les sources, les
scores, le tableau des items écartés et la ligne de coût sont générés par le pipeline à partir
des données. Un modèle ne doit jamais recopier un chiffre qu'il pourrait déformer, et un test
automatique le vérifie.

## Passer à vingt items

Deux réglages, aucun code :

1. `config/write.toml` : `digest_size = 20`
2. `config/questions.toml` : `min_adjusted_score` doit descendre, sinon le plancher coupe avant
   la taille. Mesuré sur la nuit du 2026-09-16 :

   | Plancher | Items admis |
   | --- | --- |
   | 2,0 (valeur actuelle) | 14 |
   | 1,9 | 19 |
   | 1,8 | 24 |

   Pour vingt items, viser **1,9**.

Le coût suit le volume, et pas seulement la sortie :

- l'état passe d'environ 63 Ko à 158 Ko pour vingt items, soit **≈ 61 000 tokens d'entrée** ;
- la sortie double au moins : 8 428 tokens pour huit items sur le dernier run, à quoi il faut
  ajouter la variance mesurée, qui atteint un facteur 2,3 entre deux runs identiques.

Repère utile : avec le tokenizer de Claude, l'état consomme **environ 2,6 caractères par token**,
pas 4. Les 63 103 caractères du dump correspondent aux 24 242 tokens d'entrée réellement facturés.
Une estimation à 4 caractères par token sous-estime donc la facture de moitié.

## Ce que la comparaison a déjà montré

Sur les mêmes huit items du 2026-09-16, trois modèles ont été essayés et lus :

| Modèle | Coût de la nuit | Ce qui a été observé |
| --- | --- | --- |
| `deepseek/deepseek-v4-flash` | 0,0011 USD | français correct, mais le jargon est empilé sans être expliqué |
| `mistralai/mistral-medium-3` | 0,0101 USD | le plus complet techniquement, deux fautes de français relevées |
| `anthropic/claude-sonnet-5` | 0,1328 USD | le seul qui explique le raisonnement, retenu pour ça |

Le détail, y compris les défauts trouvés et les corrections apportées au prompt, est dans le
[Journal de mise en œuvre](implementation-journal.md).

Deux conseils de méthode : comparer les modèles **sur le même dump**, sinon le hasard du run
brouille la comparaison ; et juger sur le français et la vulgarisation, pas sur la fiche
technique — c'est le seul critère qui compte ici, et il ne se lit sur aucun tableau de prix.
