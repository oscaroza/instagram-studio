# Instagram Studio V1

Mini studio personnel pour préparer et publier des Reels Instagram avec génération de captions par **Cerebras**.

## Ce que fait la V1

- Interface responsive, utilisable sur iPhone.
- Upload temporaire MP4/MOV/M4V sans charger tout le fichier en RAM.
- Génération IA via l'API Cerebras (`gpt-oss-120b` par défaut).
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
  ├─ Cerebras API
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
CEREBRAS_API_KEY=...
INSTAGRAM_ACCESS_TOKEN=...
INSTAGRAM_USER_ID=...
```

Pour OAuth Instagram complet :

```env
INSTAGRAM_APP_ID=...
INSTAGRAM_APP_SECRET=...
INSTAGRAM_REDIRECT_URI=https://TON-SERVICE.onrender.com/auth/instagram/callback
```

Ne jamais committer `.env`.

## Cerebras

Le modèle est configurable :

```env
CEREBRAS_MODEL=gpt-oss-120b
```

Si Cerebras modifie son catalogue ou ses limites, il suffit de changer cette variable dans Render sans modifier le code.

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

- Les tokens API sont lus uniquement côté serveur.
- Aucun token n'est renvoyé au JS de l'interface.
- `.env` est ignoré par Git.
- Le callback OAuth masque le token à l'écran.
- Le flux OAuth utilise un `state` signé.

## Limites V1

- Le stockage temporaire Render peut disparaître à un redémarrage/spin-down.
- Pas encore de programmation automatique par heure/date.
- Pas encore de récupération des Insights.
- Pas encore d'analyse automatique du contenu vidéo par vision.
- OAuth Meta demandera de terminer la configuration de l'app dans Meta for Developers.

Ces éléments sont prévus pour V2/V3.
