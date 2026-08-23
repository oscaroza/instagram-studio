# Instagram Studio V2

Studio personnel FastAPI pour préparer, programmer et publier des Reels, photos JPEG et carrousels photo Instagram. La V2 conserve le flow de publication Reel immédiate de la V1 et l’OAuth Instagram direct.

## Fonctionnalités

- accès par code côté serveur avec cookie de session sécurisé, limitation des essais et historique de connexion respectueux de la vie privée ;
- génération de texte avec Groq en conservant les noms `CEREBRAS_*` ;
- publication immédiate d’un Reel normal ou Trial Reel ;
- publication immédiate ou programmée d’une photo JPEG ou d’un carrousel de 2 à 10 JPEG ;
- calendrier mensuel et publications programmées côté serveur ;
- bibliothèque média Cloudinary pour les publications programmées, indexée dans MongoDB, avec suppression manuelle ;
- notifications Web Push PWA : 30 minutes avant, succès, échec, workflow musique, connexion et santé du token Instagram ;
- icône Apple/PWA et interface iPhone ;
- compteur Meta des publications API sur les dernières 24 heures ;
- token Instagram longue durée chiffré dans MongoDB et rafraîchi automatiquement ;
- vérification avant envoi et protection de 15 minutes contre une double publication identique.

Le workflow musique crée un brouillon dans le Studio, copie le texte et ouvre Instagram : l’API Meta ne permet pas de choisir une musique ni de créer un brouillon natif dans l’app Instagram. Ce workflow et le mode Trial restent réservés aux Reels.

Un upload destiné à une publication immédiate conserve le flow V1 et reste sur l’URL publique temporaire de Render. La copie Cloudinary n’est créée qu’au moment où l’utilisateur confirme une programmation. L’option **Couper le son** retire localement la piste audio d’un Reel immédiat et utilise la transformation Cloudinary `audio_codec: none` en programmation.

## Architecture

```text
iPhone / navigateur
  └─ Instagram Studio PWA sur Render
       ├─ Groq : génération des textes
       ├─ Instagram API : publication et quota
       ├─ Cloudinary : vidéos et photos durables
       └─ MongoDB Atlas : calendrier, métadonnées, push, token chiffré
```

MongoDB ne stocke pas les vidéos. La limite de 512 Mo du cluster Atlas Free sert donc aux petits documents et non aux médias.

## Variables Render

Voir `.env.example` et `render.yaml`. Les secrets ne doivent jamais être commités.

```env
APP_BASE_URL=https://TON-SERVICE.onrender.com
STUDIO_ACCESS_CODE=...
STUDIO_IDLE_MINUTES=5
# Facultatif : les valeurs par défaut ci-dessous sont déjà actives
LOGIN_MAX_ATTEMPTS=5
LOGIN_WINDOW_MINUTES=15
LOGIN_LOCKOUT_MINUTES=15
CEREBRAS_API_KEY=TA_CLE_GROQ
CEREBRAS_BASE_URL=https://api.groq.com/openai/v1
CEREBRAS_MODEL=openai/gpt-oss-20b

MONGODB_URI=mongodb+srv://UTILISATEUR:MOT_DE_PASSE_ENCODE@instagram-studio.kecpds1.mongodb.net/?retryWrites=true&w=majority&appName=instagram-studio
MONGODB_DATABASE=instagram_studio

CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
CLOUDINARY_FOLDER=instagram-studio

VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
VAPID_SUBJECT=mailto:ton-adresse@example.com
```

Le mot de passe MongoDB doit être encodé pour une URL s’il contient des caractères spéciaux. Comme un ancien mot de passe a été partagé dans une conversation, il doit être révoqué et remplacé dans Atlas avant le déploiement.

Dans MongoDB Atlas, ajouter l’environnement Render à **Security → Network Access → IP Access List**. Sans cette autorisation, le Studio affiche `Connexion Atlas impossible`. Ne jamais contourner ce problème avec `tlsInsecure=true`.

Pour générer les deux clés VAPID sans les afficher dans le terminal :

```bash
.venv/bin/python scripts/generate_vapid_env.py
```

Les valeurs sont écrites dans `.vapid.env`, fichier local ignoré par Git. Copie-les ensuite dans Render, puis supprime ce fichier si tu le souhaites.

## Connexion Instagram et token longue durée

Configurer dans Render :

```env
INSTAGRAM_APP_ID=...
INSTAGRAM_APP_SECRET=...
INSTAGRAM_REDIRECT_URI=https://TON-SERVICE.onrender.com/auth/instagram/callback
INSTAGRAM_API_BASE=https://graph.instagram.com
INSTAGRAM_API_VERSION=v26.0
ENABLE_TRIAL_REELS=true
```

Le bouton **Connecter Instagram** échange automatiquement le token court contre un token longue durée. Le token est montré une fois sur l’écran de succès, comme dans la V1, puis chiffré dans MongoDB avec `APP_SECRET_KEY`. Après 30 jours, le Studio tente de le renouveler automatiquement avant utilisation. Il ne doit jamais être journalisé.

Le Studio peut aussi envoyer une notification si ce renouvellement échoue ou si le token stocké arrive à moins de 7 jours de son expiration. Cette alerte est activable séparément dans l’onglet **Notifications**.

`INSTAGRAM_ACCESS_TOKEN` et `INSTAGRAM_USER_ID` restent acceptés comme solution de secours afin de ne pas casser la V1.

## Notifications sur iPhone

1. Ouvrir le Studio dans Safari.
2. Partager → **Sur l’écran d’accueil**.
3. Ouvrir le Studio depuis sa nouvelle icône.
4. Aller dans **Notifications** et appuyer sur **Activer les notifications**.

Web Push nécessite iOS/iPadOS 16.4 ou plus récent et l’application ajoutée à l’écran d’accueil.

## Sécurité de session

La session d’accès est renouvelée uniquement lors d’une interaction avec le Studio. Après 5 minutes sans activité, le serveur refuse les nouvelles actions et l’interface affiche une bannière demandant d’actualiser la page pour se reconnecter. Le même contrôle s’applique au retour dans la PWA après plus de 5 minutes en arrière-plan. Cette déconnexion ne supprime ni le token Instagram chiffré ni les programmations.

Par défaut, 5 codes incorrects sur une fenêtre de 15 minutes bloquent les nouveaux essais pendant 15 minutes. L’historique affiché dans **Réglages** est conservé 90 jours : il contient seulement la date, le type d’appareil, le navigateur et le résultat. Le code saisi et l’adresse IP brute ne sont jamais enregistrés.

## Programmation

Le planificateur tourne dans le même service Render et vérifie les publications toutes les 30 secondes. UptimeRobot peut appeler `/health` régulièrement pour éviter la veille du service. Les tâches sont réclamées de façon atomique dans MongoDB afin d’éviter une double publication, et une tâche interrompue est remise en file après 15 minutes.

Une programmation avec musique ne publie pas via l’API : à l’heure prévue, elle passe à **À finaliser dans Instagram** et envoie une notification.

## Développement et tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

```bash
python -m pytest -q
node --check app/static/app.js
```

Le serveur continue de démarrer en mode V1 si MongoDB est temporairement indisponible. Une panne MongoDB après une publication Instagram réussie ne transforme pas ce succès en erreur côté utilisateur.
