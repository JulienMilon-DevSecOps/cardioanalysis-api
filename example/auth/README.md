# Supabase self-hosted — déployer l'authentification chez soi

Ce dossier contient un stack **Supabase self-hosted** (Docker Compose) prêt à
déployer sur sa propre machine : Postgres, Auth (GoTrue), API gateway,
PostgREST, Realtime, Storage, Studio, Supavisor (pooler)...

`cardioanalysis-api` l'utilise pour l'authentification (vérification des JWT
émis par Supabase Auth, endpoint `GET /me`) et comme base PostgreSQL, mais le
stack lui-même est **générique** : il peut servir d'authentification globale à
n'importe quelle application.

> Aucun secret n'est fourni : toutes les clés du `.env.example` valent
> `change-me-run-utils-generate-keys` et doivent être générées (étape 2).

**Sommaire**

1. [Origine du contenu](#1-origine-du-contenu)
2. [Prérequis](#2-prérequis)
3. [Installation](#3-installation)
4. [Démarrer le stack](#4-démarrer-le-stack)
5. [Vérifier : créer un utilisateur et obtenir un JWT](#5-vérifier--créer-un-utilisateur-et-obtenir-un-jwt)
6. [Se connecter à Postgres](#6-se-connecter-à-postgres)
7. [Brancher cardioanalysis-api](#7-brancher-cardioanalysis-api)
8. [Activer le stack : seulement pour les tests ou en permanence](#8-activer-le-stack--seulement-pour-les-tests-ou-en-permanence)
9. [Arrêter, réinitialiser](#9-arrêter-réinitialiser)
10. [Déployer sur un serveur : sécurité](#10-déployer-sur-un-serveur--sécurité)
11. [Mettre à jour Supabase](#11-mettre-à-jour-supabase)

---

## 1. Origine du contenu

Repris du dossier [`docker/`](https://github.com/supabase/supabase/tree/master/docker)
du dépôt officiel `supabase/supabase` (licence
[Apache-2.0](https://github.com/supabase/supabase/blob/master/LICENSE)), versions
d'images figées dans `docker-compose.yml` (Studio `2026.08.03`, Auth `v2.189.0`,
Postgres `17.6.1.136`, Supavisor `2.9.5`...).

| Élément | Origine |
|---------|---------|
| `docker-compose.yml` | Officiel ; seuls les secrets d'exemple des hooks Auth (lignes commentées `GOTRUE_HOOK_SEND_*_SECRETS`) sont remplacés par `<standard-base64-secret>` pour ne pas déclencher les scanners de secrets |
| `docker-compose.caddy.yml`, `run.sh`, `reset.sh`, `utils/`, `volumes/` | Officiel, non modifié (`reset.sh` : voir section 9) |
| `.env.example` | Officiel, secrets de démonstration remplacés par `change-me-run-utils-generate-keys`, section pgAdmin ajoutée |
| `docker-compose.minimal.yml` | Ajouté : auth + base de données sans Studio (machines avec peu de RAM) |
| `docker-compose.pgadmin.yml` | Ajouté : pgAdmin pour le développement local |

Documentation officielle de référence :
[Self-Hosting with Docker](https://supabase.com/docs/guides/self-hosting/docker).

---

## 2. Prérequis

- Docker et **Docker Compose ≥ 2.24.4** (balise `!override` du mode minimal) : `docker compose version`
- `openssl` (génération des clés)
- RAM : ~2 Go libres pour le stack complet, beaucoup moins en mode minimal

---

## 3. Installation

### 3.1 Copier le dossier

Le stack crée ses données dans `volumes/db/data/` et `volumes/storage/` : le
faire tourner **hors du dossier versionné** `example/auth/`.

```bash
# Depuis la racine de cardioanalysis-api : local/ est gitignoré, et c'est
# l'emplacement attendu par défaut par tests/conftest.py (voir section 8)
mkdir -p local
cp -r example/auth local/auth
cd local/auth
```

Tout autre emplacement fonctionne (ex : `~/supabase`), voir `SUPABASE_DIR`
en section 8.

### 3.2 Créer le `.env` et générer les secrets

```bash
cp .env.example .env
sh utils/generate-keys.sh --update-env
rm -f .env.old   # sauvegarde laissée par le script (contient les anciennes valeurs)
```

Le script génère et écrit dans `.env` : `POSTGRES_PASSWORD`, `JWT_SECRET`,
`ANON_KEY`, `SERVICE_ROLE_KEY`, `DASHBOARD_PASSWORD`, `SECRET_KEY_BASE`,
`VAULT_ENC_KEY`, `PG_META_CRYPTO_KEY`, les jetons Logflare, les clés S3 et
`MINIO_ROOT_PASSWORD`.

- `JWT_SECRET` : clé HS256 qui signe tous les JWT des utilisateurs. **C'est elle
  que l'application utilise pour vérifier les jetons.**
- `ANON_KEY` : clé publique (rôle `anon`), à passer en en-tête `apikey` pour
  appeler l'API Auth depuis un client.
- `SERVICE_ROLE_KEY` : clé d'administration (contourne les droits) — ne
  jamais l'exposer côté client.

Chaque environnement (poste de dev, serveur) doit avoir **ses propres
secrets** : relancer le script pour chacun, ne jamais copier un `.env` d'un
environnement à l'autre.

### 3.3 Valeurs à adapter à la main dans `.env`

| Variable | Valeur conseillée | Pourquoi |
|----------|-------------------|----------|
| `POOLER_TENANT_ID` | un identifiant court, ex : `mon-projet-dev` | Fait partie du nom d'utilisateur Postgres côté hôte (section 6) |
| `ENABLE_EMAIL_AUTOCONFIRM` | `true` en local | Pas de serveur SMTP en local : sans ça, un nouvel utilisateur doit confirmer son e-mail |
| `DASHBOARD_USERNAME` | à changer | Identifiant de connexion à Studio (mot de passe : `DASHBOARD_PASSWORD`) |
| `PGADMIN_DEFAULT_EMAIL` / `PGADMIN_DEFAULT_PASSWORD` | à changer | Uniquement si pgAdmin est utilisé |
| `SITE_URL`, `API_EXTERNAL_URL`, `SUPABASE_PUBLIC_URL` | `localhost` en local | URL publiques sur un serveur (section 10) |

---

## 4. Démarrer le stack

Commandes à lancer depuis le dossier du stack (là où se trouve le `.env`).

| Mode | Commande | Services |
|------|----------|----------|
| **Complet** | `docker compose up -d` | Tous (Studio compris) |
| **Minimal** (auth + base, sans Studio) | `docker compose -f docker-compose.yml -f docker-compose.minimal.yml up -d db supavisor auth api-gw` | 4 conteneurs |
| **Base seule** (tests d'intégration SQL) | `docker compose up -d db supavisor` | 2 conteneurs |
| **+ pgAdmin** | `docker compose -f docker-compose.yml -f docker-compose.pgadmin.yml up -d pgadmin` | pgAdmin (+ db) |

Le mode minimal existe parce que dans `docker-compose.yml`, la gateway attend
que Studio soit *healthy* : sur une machine avec peu de RAM, Studio (Next.js)
peut planter et bloquer toute l'API. Studio n'est pas nécessaire au
fonctionnement de l'auth.

Pour ne pas répéter les `-f`, les overrides peuvent être enregistrés dans
`COMPOSE_FILE` du `.env` via le script officiel :

```bash
sh run.sh config add minimal pgadmin   # COMPOSE_FILE=docker-compose.yml:docker-compose.minimal.yml:docker-compose.pgadmin.yml
sh run.sh config show
docker compose up -d db supavisor auth api-gw pgadmin
```

Accès une fois démarré :

| Service | URL |
|---------|-----|
| API gateway (Auth : `/auth/v1/*`, REST : `/rest/v1/*`) | `http://localhost:8000` (`API_GW_HTTP_PORT`) |
| Studio (mode complet) | `http://localhost:8000` (identifiants `DASHBOARD_*`) |
| Postgres via le pooler | `localhost:5432` (`POSTGRES_PORT`) |
| pgAdmin | `http://localhost:5050` (`PGADMIN_PORT`) |

---

## 5. Vérifier : créer un utilisateur et obtenir un JWT

```bash
set -a; . ./.env; set +a   # charge ANON_KEY, SERVICE_ROLE_KEY... dans le shell

# Santé du service Auth
curl -s http://localhost:8000/auth/v1/health -H "apikey: $ANON_KEY"

# Inscription (e-mail confirmé automatiquement si ENABLE_EMAIL_AUTOCONFIRM=true)
curl -s -X POST http://localhost:8000/auth/v1/signup \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"un-mot-de-passe-solide"}'

# Connexion : la réponse contient access_token (le JWT)
curl -s -X POST "http://localhost:8000/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"un-mot-de-passe-solide"}'
```

Créer un utilisateur côté administration (sans inscription publique, e-mail
déjà confirmé) :

```bash
curl -s -X POST http://localhost:8000/auth/v1/admin/users \
  -H "apikey: $SERVICE_ROLE_KEY" -H "Authorization: Bearer $SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"un-mot-de-passe-solide","email_confirm":true}'
```

Le JWT obtenu est signé avec `JWT_SECRET` (HS256), avec l'audience
`authenticated` et l'identifiant de l'utilisateur dans `sub`.

---

## 6. Se connecter à Postgres

Le port exposé sur l'hôte (`POSTGRES_PORT`, 5432) n'est **pas** Postgres
directement mais le pooler **Supavisor**. Il exige un nom d'utilisateur au
format **`<rôle>.<POOLER_TENANT_ID>`**, sinon l'erreur (peu explicite) est
`no tenant identifier provided`.

```bash
psql "postgresql://postgres.mon-projet-dev:$POSTGRES_PASSWORD@localhost:5432/postgres"
```

À l'intérieur du réseau Docker (pgAdmin, autres conteneurs), Postgres est
joignable directement sur `db:5432` avec un utilisateur simple (`postgres`).

### Créer un rôle applicatif dédié (recommandé)

Une application ne devrait pas se connecter avec le super-utilisateur
`postgres`. Exemple de rôle limité à un schéma (remplacer `mon_app`,
`mon_schema` et le mot de passe) :

```bash
docker exec -i supabase-db psql -U postgres <<'SQL'
create role mon_app with login password 'mot-de-passe-du-role' noinherit;
create schema if not exists mon_schema authorization mon_app;
grant usage on schema mon_schema to mon_app;
-- Les tables sans schéma explicite sont créées dans mon_schema
alter role mon_app set search_path = mon_schema, public;
alter default privileges in schema mon_schema
    grant select, insert, update, delete on tables to mon_app;
alter default privileges in schema mon_schema
    grant usage, select on sequences to mon_app;
SQL
```

L'application s'y connecte ensuite via le pooler avec l'utilisateur
`mon_app.<POOLER_TENANT_ID>`.

Pour que ce rôle soit recréé automatiquement à chaque initialisation de la
base, placer le script SQL dans `volumes/db/` et le monter dans le service
`db` sous `/docker-entrypoint-initdb.d/init-scripts/` (le mot de passe peut
être lu depuis une variable d'environnement du conteneur, voir
`volumes/db/roles.sql` pour l'exemple officiel). Les scripts d'initialisation
ne s'exécutent que sur une base vide (section 9).

---

## 7. Brancher cardioanalysis-api

Dans le `.env` à la racine de `cardioanalysis-api` (modèle : `.env.example`) :

| Variable de l'API | Valeur |
|-------------------|--------|
| `SUPABASE_URL` | `http://localhost:8000` |
| `SUPABASE_JWT_SECRET` | `JWT_SECRET` du `.env` du stack |
| `DB_HOST` / `DB_PORT` | `localhost` / `5432` |
| `DB_NAME` | `postgres` |
| `DB_USER` / `DB_PASSWORD` | `<rôle>.<POOLER_TENANT_ID>` et son mot de passe (section 6) |
| `DB_*_TEST` | Idem pour les tests d'intégration |

⚠️ La gateway Supabase écoute sur le port **8000**, celui d'`uvicorn` par
défaut : lancer l'API sur un autre port.

```bash
uvicorn app.main:app --reload --port 8001
```

Obtenir un JWT (section 5), puis :

```bash
curl -s http://localhost:8001/me -H "Authorization: Bearer <access_token>"
```

---

## 8. Activer le stack : seulement pour les tests ou en permanence

### Seulement pendant les tests (par défaut)

`tests/conftest.py` démarre le stack **automatiquement** si des tests marqués
`@pytest.mark.integration` vont s'exécuter, puis l'arrête à la fin de la
session (seulement s'il l'a lui-même démarré). Seuls `db` et `supavisor` sont
lancés : c'est tout ce dont ont besoin les tests d'intégration (SQL direct).

```bash
pytest -m "not integration"   # tests unitaires : Docker n'est jamais touché
pytest                        # + intégration : démarrage auto (~1 min), tests, arrêt auto
```

Emplacement du stack utilisé par `conftest.py` (variables d'environnement) :

| Variable | Défaut | Rôle |
|----------|--------|------|
| `SUPABASE_DIR` | `local/auth` (relatif à la racine du projet) | Dossier du stack |
| `SUPABASE_ENV_FILE` | `.env` | Fichier d'env du stack, relatif à `SUPABASE_DIR` |

```bash
SUPABASE_DIR=~/supabase pytest
```

Les tests d'intégration sont **ignorés** (skip) si `DB_HOST_TEST` n'est pas
défini : sans stack, `pytest` reste utilisable.

### En permanence

Démarrer le stack une fois (section 4) : les services ont la politique
`restart: unless-stopped`.

- Il **redémarre avec Docker** (reboot de la machine) tant qu'il n'a pas été
  arrêté explicitement.
- Un `docker compose stop` / `down` le laisse arrêté, même après un reboot.
- `conftest.py` détecte un stack déjà lancé et ne l'arrête pas en fin de tests.

---

## 9. Arrêter, réinitialiser

```bash
docker compose stop                  # arrêt, données et conteneurs conservés (redémarrage rapide)
docker compose down                  # supprime les conteneurs, données conservées (volumes/db/data)
```

Réinitialisation complète (⚠️ supprime **toutes** les données, `.env` conservé) :

```bash
docker compose down -v --remove-orphans
sudo rm -rf volumes/db/data volumes/storage   # fichiers appartenant à l'utilisateur du conteneur
```

`reset.sh` (officiel) fait la même chose et remet en plus le `.env` à zéro,
mais il attend le fichier `dev/docker-compose.dev.yml` du dépôt officiel, non
repris ici : sans lui, le script s'arrête en erreur.

Les scripts d'initialisation SQL ne s'exécutent qu'au premier démarrage, sur
un dossier `volumes/db/data/` vide. Après une modification de ces scripts, il
faut réinitialiser la base (perte des données).

---

## 10. Déployer sur un serveur : sécurité

Le stack peut tourner sur un serveur (NAS, VPS) avec les mêmes fichiers, en
plus de ces précautions :

- **Secrets dédiés** au serveur (section 3.2), jamais ceux du poste de dev.
- **Ne jamais exposer Studio, pgAdmin ni Postgres sur internet.** Rester en
  accès réseau local / VPN (WireGuard, Tailscale), ou passer par un reverse
  proxy avec TLS : `docker-compose.caddy.yml` (variable `PROXY_DOMAIN`,
  voir [HTTPS proxy](https://supabase.com/docs/guides/self-hosting/self-hosted-proxy-https)).
- Pas d'override pgAdmin sur un serveur.
- `ENABLE_EMAIL_AUTOCONFIRM=false` et un vrai serveur SMTP (`SMTP_*`) si des
  utilisateurs s'inscrivent eux-mêmes, ou `DISABLE_SIGNUP=true` si les comptes
  sont créés uniquement par l'administrateur (section 5).
- `SITE_URL`, `API_EXTERNAL_URL`, `SUPABASE_PUBLIC_URL` : URL publiques réelles.
- Stocker `volumes/db/data/` et `volumes/storage/` sur un disque fiable et les
  **sauvegarder** (`pg_dump` régulier).

---

## 11. Mettre à jour Supabase

Les versions des images sont figées dans `docker-compose.yml`. Pour monter de
version, reprendre les fichiers du dossier `docker/` officiel (en conservant
`.env`, `docker-compose.minimal.yml` et `docker-compose.pgadmin.yml`) et
suivre le [guide de mise à jour](https://supabase.com/docs/guides/self-hosting/updating).
**Sauvegarder la base avant toute mise à jour.**
