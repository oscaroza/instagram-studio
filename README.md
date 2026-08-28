# Instagram Studio V2

Studio personnel FastAPI pour préparer, programmer et publier des Reels, photos JPEG, carrousels photo/vidéo et Stories Instagram. La V2 conserve le flow de publication Reel immédiate de la V1 et l’OAuth Instagram direct.

## Fonctionnalités

- accès par code côté serveur avec limitation des essais, alerte de sécurité, historique et blocage manuel réversible des appareils reconnus ;
- génération de texte avec Groq en conservant les noms `CEREBRAS_*` ;
- publication immédiate d’un Reel normal ou Trial Reel ;
- publication immédiate ou programmée d’une photo JPEG ou d’un carrousel de 2 à 10 photos, vidéos, ou médias mixtes ;
- publication immédiate ou programmée d’une Story photo JPEG ou vidéo, avec suivi dans le calendrier et notifications ;
- calendrier mensuel, hebdomadaire et liste, avec déplacement des programmations par glisser-déposer ;
- bibliothèque média Cloudflare R2 avec recherche, filtres par type/date/poids/utilisation, suppression manuelle et compatibilité des anciens médias Cloudinary ;
- réorganisation des médias d’un carrousel par glisser-déposer, au doigt ou à la souris ;
- notifications Web Push PWA : 30 minutes avant, succès, échec, workflow musique, connexion et santé du token Instagram ;
- icône Apple/PWA et interface iPhone ;
- compteur Meta des publications API sur les dernières 24 heures ;
- dashboard statistique avec comparaison sur 7, 30 ou 90 jours et courbe d’évolution fondée sur les relevés MongoDB ;
- assistant Groq conversationnel dont l’historique reste dans MongoDB et n’est pas renvoyé automatiquement à Groq ;
- générateur Groq de trois protocoles vidéo orientés croissance, fondés sur les statistiques anonymisées et un brief matériel/contraintes, avec sauvegarde du dernier plan dans MongoDB ;
- file Auto-pilot avec analyse visuelle de trois images représentatives maximum, proposition de créneaux fondée sur les statistiques et validation humaine obligatoire avant chaque programmation ;
- connexion Face ID, Touch ID ou passkey WebAuthn, avec le code d’accès conservé comme secours ;
- token Instagram longue durée chiffré dans MongoDB et rafraîchi automatiquement ;
- vérification avant envoi et protection de 15 minutes contre une double publication identique.

Le workflow musique crée un brouillon dans le Studio, copie le texte et ouvre Instagram : l’API Meta ne permet pas de choisir une musique ni de créer un brouillon natif dans l’app Instagram. Les Stories API sont publiées sans légende, musique, lien ou sticker interactif ; le texte doit être intégré directement dans leur image ou vidéo. `ENABLE_INSTAGRAM_STORIES=false` permet de masquer la capability si Meta refuse les Stories pour le compte connecté.

Un upload destiné à une publication immédiate conserve le flow V1 et reste sur l’URL publique temporaire de Render. La copie R2 n’est créée qu’au moment où l’utilisateur confirme une programmation. Les fichiers de 16 Mo et plus utilisent automatiquement un envoi multipart. L’option **Couper le son** retire la piste audio avant le stockage R2. Les anciens documents Cloudinary continuent d’utiliser leur transformation `audio_codec: none`.

## Architecture

```text
iPhone / navigateur
  └─ Instagram Studio PWA sur Render
       ├─ Groq : génération des textes
       ├─ Instagram API : publication et quota
       ├─ Cloudflare R2 : nouvelles vidéos et photos durables
       ├─ Cloudinary : anciens médias conservés pendant la transition
       └─ MongoDB Atlas : calendrier, métadonnées, push, token chiffré
```

MongoDB ne stocke pas les vidéos. La limite de 512 Mo du cluster Atlas Free sert donc aux petits documents et non aux médias.

## Variables Render

Voir `.env.example` et `render.yaml`. Les secrets ne doivent jamais être commités.

```env
APP_BASE_URL=https://TON-SERVICE.onrender.com
STUDIO_ACCESS_CODE=...
STUDIO_IDLE_MINUTES=10
# Facultatif : les valeurs par défaut ci-dessous sont déjà actives
LOGIN_MAX_ATTEMPTS=5
LOGIN_WINDOW_MINUTES=15
LOGIN_LOCKOUT_MINUTES=15
CEREBRAS_API_KEY=TA_CLE_GROQ
CEREBRAS_BASE_URL=https://api.groq.com/openai/v1
CEREBRAS_MODEL=openai/gpt-oss-20b
CEREBRAS_VISION_MODEL=qwen/qwen3.6-27b
# Facultatif : 3 images maximum et 3 publications par semaine par défaut
AUTOPILOT_FRAME_COUNT=3
AUTOPILOT_DEFAULT_POSTS_PER_WEEK=3
ENABLE_INSTAGRAM_STORIES=true

MONGODB_URI=mongodb+srv://UTILISATEUR:MOT_DE_PASSE_ENCODE@instagram-studio.kecpds1.mongodb.net/?retryWrites=true&w=majority&appName=instagram-studio
MONGODB_DATABASE=instagram_studio

MEDIA_STORAGE_BACKEND=r2
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=instagram-studio
R2_PUBLIC_BASE_URL=https://media.ton-domaine.com
R2_FOLDER=instagram-studio
CLOUDFLARE_ANALYTICS_API_TOKEN=...
CLOUDFLARE_BILLING_API_TOKEN=...
R2_MAX_STORAGE_GB=9

# Garde ces variables tant qu’il reste d’anciens médias Cloudinary à supprimer.
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
CLOUDINARY_FOLDER=instagram-studio

VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
VAPID_SUBJECT=mailto:ton-adresse@example.com
```

Auto-pilot enregistre d’abord les médias dans R2, puis Groq Vision analyse quelques images extraites sans recevoir les tokens Instagram. Le planificateur tient compte des publications déjà programmées. Les propositions restent modifiables et aucune publication n’est ajoutée au calendrier avant une validation explicite. Si l’option musique est activée, le créneau peut être proposé mais la finalisation dans Instagram reste manuelle.

Le mot de passe MongoDB doit être encodé pour une URL s’il contient des caractères spéciaux. Comme un ancien mot de passe a été partagé dans une conversation, il doit être révoqué et remplacé dans Atlas avant le déploiement.

## Configuration Cloudflare R2 gratuite

1. Dans Cloudflare, ouvrir **Storage & databases → R2** et activer R2.
2. Créer un bucket `instagram-studio` en classe **Standard**. Ne pas choisir **Infrequent Access**, qui n’a pas de quota gratuit.
3. Dans **R2 → Manage API Tokens**, créer un token limité à ce bucket avec la permission **Object Read & Write**. Copier l’Access Key ID et la Secret Access Key dans Render ; le secret ne sera plus affiché ensuite.
4. Dans le bucket, ouvrir **Settings → Public access** et connecter de préférence un domaine comme `media.ton-domaine.com`. L’URL `r2.dev` peut servir pour un test, mais Cloudflare la réserve au développement et la limite en débit.
5. Mettre l’adresse HTTPS publique exacte, sans slash final, dans `R2_PUBLIC_BASE_URL`.
6. Dans **Manage Account → API Tokens**, créer un token personnalisé séparé avec uniquement **Account → Account Analytics → Read**, limité à ton compte. Mettre sa valeur dans `CLOUDFLARE_ANALYTICS_API_TOKEN` sur Render.
7. Créer un second token en lecture seule avec **Account → Billing → Read**, limité au même compte, puis le mettre dans `CLOUDFLARE_BILLING_API_TOKEN`. Le Studio utilise ce token pour récupérer le cycle réel de facturation et, lorsque l’endpoint Billing Usage est disponible pour le compte, les compteurs qui alimentent la facturation. Si cet endpoint Cloudflare encore restreint refuse l’accès, l’interface l’indique et affiche uniquement Analytics sur la période de facturation comme valeur de secours clairement identifiée. Aucun de ces tokens n’est envoyé au navigateur.
8. Dans **Settings → CORS Policy**, ajouter la règle ci-dessous en remplaçant l’origine par l’adresse exacte du Studio Render, sans slash final :

```json
[
  {
    "AllowedOrigins": ["https://TON-SERVICE.onrender.com"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["Range"],
    "ExposeHeaders": ["ETag", "Content-Length", "Content-Range"],
    "MaxAgeSeconds": 3600
  }
]
```

R2 Standard inclut actuellement 10 Go-mois, 1 million d’opérations de classe A, 10 millions d’opérations de classe B et la sortie Internet gratuite. Le Studio impose en plus `R2_MAX_STORAGE_GB=9` et mesure tout le bucket avant chaque envoi : si le prochain fichier dépasse cette limite, il est refusé avant l’upload. Utilise un bucket dédié au Studio pour que ce calcul couvre bien tous ses objets. Les alertes de budget Cloudflare restent recommandées, mais elles préviennent seulement et ne bloquent pas les dépenses.

`MEDIA_STORAGE_BACKEND=auto` sélectionne R2 dès que toutes ses variables sont présentes, sinon conserve Cloudinary. Pour forcer la migration, utilise `MEDIA_STORAGE_BACKEND=r2`. Ne supprime les variables Cloudinary qu’une fois les anciens médias Cloudinary retirés de la bibliothèque ; elles restent nécessaires pour les supprimer proprement pendant la transition.

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

La session d’accès est renouvelée uniquement lors d’une interaction avec le Studio. Après 10 minutes sans activité, le serveur refuse les nouvelles actions et l’interface affiche une bannière demandant d’actualiser la page pour se reconnecter. Le même contrôle s’applique au retour dans la PWA après plus de 10 minutes en arrière-plan. Cette déconnexion ne supprime ni le token Instagram chiffré ni les programmations. Si `STUDIO_IDLE_MINUTES` existe déjà dans Render, règle aussi sa valeur sur `10`.

L’onglet **Personnaliser** permet de choisir une palette, les couleurs principales, la densité et l’arrondi des cartes. Ces préférences sont validées côté serveur puis enregistrées dans MongoDB, jamais dans le stockage du navigateur : le même thème est donc appliqué sur Mac, iPhone et les autres appareils connectés au Studio.

Par défaut, 5 codes incorrects sur une fenêtre de 15 minutes bloquent les nouveaux essais pendant 15 minutes. L’historique affiché dans **Réglages** est conservé 90 jours : il contient seulement la date, le type d’appareil, le navigateur et le résultat. Le code saisi et l’adresse IP brute ne sont jamais enregistrés.

Une préférence Push séparée prévient lorsque ce blocage automatique se déclenche. Depuis **Réglages → Sécurité des appareils**, un appareil reconnu peut aussi être bloqué manuellement — y compris si le bon code est saisi — puis débloqué. Cette protection utilise un identifiant signé conservé dans un cookie `HttpOnly` : elle peut être contournée en changeant de navigateur ou en effaçant les données du site, et ne remplace donc pas un vrai compte utilisateur ou une passkey.

## Face ID et passkeys

Ouvre d’abord le Studio avec le code, puis va dans **Réglages → Face ID et passkeys → Ajouter Face ID / passkey**. Aux connexions suivantes, l’iPhone proposera Face ID. Le visage et les données biométriques restent entièrement dans l’appareil ; MongoDB conserve seulement la clé publique nécessaire à la vérification.

`APP_BASE_URL` doit être l’adresse HTTPS exacte du service Render. La passkey est liée à ce domaine : si le Studio change de domaine, il faudra l’enregistrer de nouveau. Aucune configuration Meta ou Apple Developer supplémentaire n’est nécessaire pour l’utilisation depuis Safari ou la PWA du même domaine.

Le code d’accès reste volontairement disponible comme secours. Une passkey respecte aussi les blocages manuels et temporaires de l’appareil.

## Assistant conversationnel

Dans **Stats → Assistant Groq**, les questions sont enregistrées avec les réponses dans MongoDB pendant 90 jours et peuvent être effacées à tout moment. Chaque requête envoie à Groq uniquement la question actuelle et les statistiques agrégées/anonymisées ; les captions, hooks complets, vidéos, URL, tokens et anciennes conversations ne sont pas envoyés.

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
