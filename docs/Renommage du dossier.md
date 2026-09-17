# Renommage du dossier du projet

Note opérationnelle, à usage unique. Le projet s'appelle désormais **veille-by-jev**, mais
le dossier local s'appelle encore `Veille Audio` (avec une espace). Ce dossier n'a pas été
renommé tout de suite parce qu'il servait de racine à une session de développement en cours :
renommer la racine pendant que des commandes tournent dedans casse le shell, l'IDE et
l'observateur de fichiers.

À faire **plus tard**, hors de toute session qui travaille dans le dossier.

## Pourquoi ce n'est pas un simple `mv`

L'environnement virtuel grave le chemin absolu du projet à deux endroits au moins :

```text
.venv/lib/python3.11/site-packages/_editable_impl_veille_by_jev.pth
  → /home/toorop/Projects/ai/Veille Audio

.venv/bin/vbj
  → '''exec' '/home/toorop/Projects/ai/Veille Audio/.venv/bin/python3' "$0" "$@"
```

Après un `mv`, ces chemins pointent dans le vide : `uv run vbj` échoue avec une erreur
d'import ou « Failed to spawn ». La reconstruction de `.venv` n'est pas une précaution
facultative, c'est une étape du renommage.

Rien d'autre ne dépend du nom du dossier : `data/` et `.venv/` ne sont pas versionnés, donc
le `mv` les emporte tels quels, et l'historique git suit le dossier.

## Le script

À lancer **depuis `~/Projects/ai`**, ou tel quel : il s'y replace lui-même. Il refuse de
s'exécuter si le dossier est absent, si la cible existe déjà, ou si l'arbre git est sale.

```bash
#!/usr/bin/env bash
# Renomme le dossier du projet en "veille-by-jev" puis reconstruit l'environnement
# virtuel, dont les chemins absolus pointent vers l'ancien nom.
set -euo pipefail

cd ~/Projects/ai

# 1. Garde-fous : rien n'est touché tant que les trois conditions ne sont pas réunies.
[ -d "Veille Audio" ] || { echo "Échec : dossier 'Veille Audio' absent de $(pwd)"; exit 1; }
[ -d "veille-by-jev" ] && { echo "Échec : 'veille-by-jev' existe déjà"; exit 1; }
(cd "Veille Audio" && [ -z "$(git status --porcelain)" ]) || {
  echo "Échec : arbre git sale. Commiter ou stasher d'abord."; exit 1;
}

# 2. Renommage.
mv "Veille Audio" veille-by-jev
cd veille-by-jev

# 3. Reconstruction : .venv et .uv-cache contiennent des chemins absolus périmés.
rm -rf .venv .uv-cache
uv sync

# 4. Contrôle : la collecte déjà faite doit être reconnue, sans aucun refetch réseau.
uv run vbj collect --date 2026-09-16
```

Sortie attendue à l'étape 4 : le message « `data/2026-09-16/items.json` existe déjà — rien
à faire », avec 200 items et un coût de 0,00 USD. Si la commande relance une collecte
réseau, c'est que `data/` n'a pas suivi le `mv` : arrêter et vérifier avant d'aller plus loin.

## Ensuite : le dépôt distant

Le dépôt GitHub est public. Avant le premier `push`, vérifier qu'aucun secret ni donnée
collectée ne part avec le code (`.env` est ignoré, `data/`, `digest/` et `seen.jsonl` aussi).

```bash
cd ~/Projects/ai/veille-by-jev
git remote add origin git@github.com:<compte>/veille-by-jev.git
git push -u origin main
```

## Si on oublie cette note

Symptôme : `uv run vbj …` répond « Failed to spawn: `vbj` » ou une erreur d'import sur
`veille`, alors que le code est intact.

Correctif, dans le dossier renommé :

```bash
rm -rf .venv .uv-cache && uv sync
```
