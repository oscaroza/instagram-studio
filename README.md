# Instagram Studio V2

Studio personnel sécurisé pour préparer et publier des Reels Instagram. La V2 conserve le flow Reel V1 et ajoute progressivement le gestionnaire.

## Changements V2 sûrs

- Écran d'accès protégé par `STUDIO_ACCESS_CODE` côté serveur.
- Session signée dans un cookie `HttpOnly`, `SameSite=Lax`, `Secure` sur Render.
- Compteur Meta réel des publications API sur les dernières 24 h.
- Socle séparé pour calendrier, programmation, bibliothèque et notifications.
- Capabilities explicites pour Reel, Trial Reel, photo et carrousel.
- Trial Reel derrière `ENABLE_TRIAL_REELS=false` par défaut, sans impact sur le Reel normal.

## Ce que fait la V1

- Interface responsive, utilisable sur iPhone.
- Upload temporaire MP4/MOV/M4V sans charger tout le fichier en RAM.
- Génération IA via Groq, en conservant les noms historiques `CEREBRAS_*`.
- Caption, hashtags, hook et alt text modifiables.
- Brouillons conservés localement dans le navigateur (pas de DB requise).
- Publication d'un Reel via l'API Instagram officielle.
- Début de flux OAuth Instagram avec Instagram Login.
- Aucun secret dans le frontend ou le repo.
- `render.yaml` prêt pour Render Free.

## Architecture

```text
Browser (iPhone/PC)
  ├─ localStorage: brouillons
  └─ HTTPS
       ↓
FastAPI on Render Free
  ├─ Groq API
  ├─ Instagram API
  └─ stockage temporaire /app/uploads (éphémère)
```

Le stockage vidéo temporaire sert uniquement à la V1/test. Sur Render Free, le disque est éphémère. Pour une V2 fiable, utiliser un stockage objet (R2/S3/Cloudinary) puis donner l'URL publique à Instagram.

## Déploiement Render

1. Créer un repo GitHub avec ces fichiers.
2. Dans Render : **New > Blueprint** (ou Web Service) puis connecter le repo.
3. Ajouter les variables secrètes dans Render :

```env
APP_BASE_URL=https://TON-SERVICE.onrender.com
APP_SECRET_KEY=...
STUDIO_ACCESS_CODE=...
CEREBRAS_API_KEY=...
INSTAGRAM_ACCESS_TOKEN=...
INSTAGRAM_USER_ID=...
ENABLE_TRIAL_REELS=false
```

Pour OAuth Instagram complet :

```env
INSTAGRAM_APP_ID=...
INSTAGRAM_APP_SECRET=...
INSTAGRAM_REDIRECT_URI=https://TON-SERVICE.onrender.com/auth/instagram/callback
```

Ne jamais committer `.env`. Les clés et tokens ne sont jamais écrits dans les logs. Seul le token Instagram longue durée est révélé sur le callback OAuth protégé, à ta demande, pour pouvoir le copier dans Render.

`STUDIO_ACCESS_CODE` est obligatoire : si la variable manque, l'application reste verrouillée. Sur Render, `APP_SECRET_KEY` est générée automatiquement et `STUDIO_COOKIE_SECURE=true`.

## Groq (noms historiques Cerebras)

Le modèle est configurable :

```env
CEREBRAS_API_KEY=clé_Groq
CEREBRAS_BASE_URL=https://api.groq.com/openai/v1
CEREBRAS_MODEL=openai/gpt-oss-20b
```

Les noms `CEREBRAS_*` sont conservés pour éviter une migration inutile, mais les valeurs doivent correspondre à Groq.

## Instagram / Meta

Cette V1 vise **Instagram API with Instagram Login** pour les comptes professionnels (Business/Creator). La publication d'un Reel suit le flux :

1. création du conteneur `/media`,
2. attente de traitement,
3. publication `/media_publish`.

Pour tester rapidement un compte personnel/Creator dont tu contrôles l'app Meta, tu peux renseigner directement `INSTAGRAM_ACCESS_TOKEN` et `INSTAGRAM_USER_ID` dans Render.

## Développement local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Puis ouvrir `http://localhost:8000`.

## Sécurité

- Les tokens API sont utilisés côté serveur.
- Le token Instagram longue durée est affiché uniquement sur le callback OAuth protégé afin de pouvoir le copier dans Render; il n'est jamais journalisé.
- `.env` est ignoré par Git.
- Le callback OAuth échange automatiquement le token court contre un token longue durée.
- Le flux OAuth utilise un `state` signé.

## État de la V2

- Le stockage temporaire Render peut disparaître à un redémarrage/spin-down.
- Calendrier et programmation préparés mais volontairement inactifs tant qu'une base durable et un worker fiable ne sont pas installés.
- Bibliothèque préparée mais inactive tant qu'un stockage objet durable n'est pas configuré.
- Brouillons V1 toujours stockés localement dans le navigateur.
- Notifications préparées, sans canal activé par défaut.
- Photo et carrousel documentés comme supportés par Meta, publication à implémenter dans une étape séparée.
- Trial Reels documentés officiellement par Meta via `trial_params`; l'activation reste opt-in pour protéger le flow Reel existant.
- Pas encore de récupération des Insights.
- Pas encore d'analyse automatique du contenu vidéo par vision.
- OAuth Meta demandera de terminer la configuration de l'app dans Meta for Developers.

Ces éléments sont prévus pour V2/V3.
