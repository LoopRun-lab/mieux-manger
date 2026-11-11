# Blog satellite « Mieux manger au quotidien »

Blog Jekyll hébergé sur GitHub Pages. Un article est rédigé par l'API Anthropic et publié tous les 2 jours à 8h (heure de Paris) par GitHub Actions. Chaque article renvoie vers [protokole.fr](https://protokole.fr).

## Contenu du repo

| Fichier | Rôle |
|---|---|
| `_config.yml` | Configuration Jekyll (thème minima, plugins sitemap / seo-tag / feed) |
| `index.md`, `about.md` | Page d'accueil et page « À propos » |
| `_layouts/`, `_includes/`, `assets/css/blog.css` | Design du site (accueil, articles, bannières) |
| `_data/protokole.yml` | Textes des bannières Protokole (modifiables sans toucher au code) |
| `_posts/` | Articles publiés (un fichier Markdown par jour) |
| `scripts/topics.json` | 60 sujets SEO ; `published` est rempli quand un sujet est publié |
| `scripts/generate_post.py` | Choisit le sujet, appelle l'API, vérifie l'article, l'écrit |
| `.github/workflows/daily-post.yml` | Vérification quotidienne (publication tous les 2 jours) + lancement manuel |

## Mise en route (une seule fois)

### 1. Créer le repo
1. Sur GitHub : **New repository**, par exemple `mieux-manger`, en **Public** (GitHub Pages gratuit exige un repo public).
2. Ne cochez rien (pas de README). Puis, dans ce dossier :
   ```bash
   git init -b main
   git add .
   git commit -m "Blog satellite : structure initiale"
   git remote add origin https://github.com/VOTRE-USER/mieux-manger.git
   git push -u origin main
   ```

### 2. Adapter l'URL du site
Dans `_config.yml`, remplacez :
- `url: "https://VOTRE-USER.github.io"` par votre nom d'utilisateur GitHub ;
- `baseurl: "/NOM-DU-REPO"` par le nom du repo (ex. `/mieux-manger`), ou `""` si vous utilisez un domaine personnalisé.

Committez et poussez.

### 3. Activer GitHub Pages
**Settings → Pages → Build and deployment** :
- Source : **Deploy from a branch**
- Branch : **main**, dossier **/ (root)** → **Save**

Le site est en ligne au bout d'une à deux minutes sur `https://VOTRE-USER.github.io/mieux-manger/`.

### 4. Ajouter la clé API
**Settings → Secrets and variables → Actions → New repository secret** :
- Name : `ANTHROPIC_API_KEY`
- Secret : votre clé (créée sur console.anthropic.com)

Optionnel, dans l'onglet **Variables** du même écran :
- `BLOG_MODEL` : modèle utilisé (`claude-opus-5-5` par défaut ; `claude-haiku-5-5` pour un coût minimal)
- `BLOG_EFFORT` : `low`, `medium` (défaut) ou `high`
- `BLOG_INTERVAL_DAYS` : jours entre deux articles (`2` par défaut ; `1` = tous les jours, `3` = tous les 3 jours)

### 5. Autoriser le workflow à pousser
**Settings → Actions → General → Workflow permissions** : cochez **Read and write permissions** → **Save**.

### 6. Tester manuellement
**Actions → Article tous les 2 jours → Run workflow**. Au bout d'une minute environ :
- succès : un commit « Article du AAAA-MM-JJ » apparaît, et l'article est en ligne après le build Pages ;
- échec : ouvrez le log de l'étape « Générer l'article » pour voir quelle règle n'est pas respectée. Rien n'est publié dans ce cas.

Le lancement manuel publie un article seulement si le dernier date d'au moins 2 jours (le tout premier passe donc toujours). Ensuite, le cron s'en charge.

## Fonctionnement

- **Un article tous les 2 jours** : le workflow tourne chaque matin, mais si le dernier article de `_posts/` date de moins de 2 jours, le script s'arrête sans appeler l'API (donc sans frais). Le rythme se règle avec la variable `BLOG_INTERVAL_DAYS`.
- **8h Paris** : GitHub ne gère que l'UTC, donc le workflow tourne à 6h et 7h UTC ; le script ne publie qu'à partir de 8h heure de Paris. GitHub Actions retarde parfois les crons de 5 à 30 minutes.
- **Contrôles avant publication** : 600 à 900 mots, mot-clé dans le titre, description de 155 caractères maximum, au moins 3 H2, une liste, exactement 2 liens vers protokole.fr (un dans le corps, un en conclusion), une phrase finale qui renvoie vers un professionnel de santé, aucun terme du type « guérir », « détox » ou « miracle ». En cas d'échec, le script fait un second essai, puis abandonne avec une erreur. Rien n'est commité.
- **Pas de doublon** : le sujet publié est marqué dans `topics.json` (`"published": "AAAA-MM-JJ"`).
- **Plus de sujets** : avec 60 sujets, il y en a pour environ quatre mois. Ajoutez des entrées à la fin de `topics.json` en gardant le même format (le workflow échoue avec un message clair quand la liste est épuisée).

## Tester en local

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # dans votre terminal, jamais dans un fichier commité
python3 scripts/generate_post.py --dry-run
```
`--dry-run` écrit l'aperçu dans `_preview/` (ignoré par git) sans toucher à `_posts/` ni à `topics.json`.

Pour vérifier un texte déjà rédigé sans appeler l'API :
```bash
python3 scripts/generate_post.py --dry-run --from-file mon-article.txt
```

## Coût indicatif
Un article fait environ 3 000 à 5 000 tokens en sortie, ce qui donne quelques centimes par article avec Claude Opus 5.5 (soit environ 0,50 à 1,50 € par mois à raison de 15 articles). Avec `BLOG_MODEL=claude-haiku-5-5`, c'est nettement moins, au prix d'une rédaction un peu moins fine. GitHub Pages et GitHub Actions sont gratuits pour un repo public.
