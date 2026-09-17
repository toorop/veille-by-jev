Tu vas développer la V1 du projet « Veille Audio » : un CLI Python qui produit chaque nuit un digest Markdown en français à partir des articles de Hacker News de la journée. Réponds-moi en français.

Documentation de cadrage, à lire dans cet ordre avant d'écrire une ligne de code :
1. README.md
2. docs/Workflow du pipeline.md
3. docs/Triage TypeSafe.md
4. docs/Handoff développeur.md

Ces notes sont la référence. Ne les modifie pas : ce sont des documents, pas du code.

Environnement : Linux, Python 3.11 ou plus récent, environnement et dépendances gérés avec uv. 

Règles de travail, impératives :
- Travaille étape par étape, dans l'ordre de la note « Handoff développeur », section « Ordre de travail conseillé ».
- Après CHAQUE étape, arrête-toi : montre-moi les fichiers créés et la sortie réelle de la commande, puis attends ma validation. N'enchaîne pas de ta propre initiative.
- Aucune donnée inventée : si une commande échoue, montre l'erreur exacte plutôt qu'un résultat plausible.
- Aucune clé d'API dans le dépôt : variables d'environnement, plus un .env.example sans valeurs.
- Avant d'ajouter une dépendance qui n'est pas listée dans le handoff, demande-moi.

Commence par l'étape 1 uniquement : la commande « veille collect », jusqu'à obtenir un data/<date>/items.json correct sur une journée réelle de Hacker News. Aucun appel à un modèle à cette étape ; je n'aurai pas de clé API à te fournir avant l'étape 3.

Quand l'étape 1 est terminée, montre-moi :
- l'arborescence créée ;
- les premières lignes de items.json ;
- le nombre d'items collectés et la plage de dates couverte ;
- la commande exacte que je dois lancer pour reproduire.
