# Configuration

Every setting is an environment variable. Nothing secret is ever in git or
in the Docker image.

- [How settings are read](#how-settings-are-read)
- [Common setups](#common-setups)
- [Reference](#reference): [database](#database) · [file storage](#file-storage) · [logging](#logging) · [app](#app)
- [Container defaults](#container-defaults)
- [AWS: ECS task definition](#aws-ecs-task-definition)
- [AWS: IAM permissions](#aws-iam-permissions)
- [CI / ECR](#ci--ecr)

## How settings are read

Highest priority first:

1. **Real environment variables**: your shell, `docker run -e`, the ECS task definition.
2. **`backend/.env`**: optional, for local development only. Copy it from
   [`backend/.env.example`](../backend/.env.example). It is git-ignored and
   excluded from the Docker image.
3. **Defaults**, listed in the tables below.

Rules worth knowing:

- `backend/.env` never overrides a variable that is already set.
- **pytest ignores `backend/.env`.** The DB-backed tests create and delete
  rows in whatever database is configured, so a `.env` pointing at a shared
  database must never become the test target. Tests only see variables you
  set explicitly in the shell.
- Deployed containers have **no** `.env`. ECS provides plain settings, plus
  secrets from Secrets Manager.
- Settings are read once, when the process starts. Restart to pick up a change.

## Common setups

**Local development, zero config.** Leave `backend/.env` absent or fully
commented out. You get SQLite and files under `backend/data/`.

**Local API against a Postgres.** In `backend/.env`:

```ini
DATABASE_URL=postgresql://postgres@localhost:5432/recon
```

**Local API against the DEV RDS**, through an SSM port-forward on local port
15432 (RDS is private to the VPC):

```ini
DB_HOST=localhost
DB_PORT=15432
DB_NAME=recon
DB_USER=...
DB_PASSWORD='...'
DB_SSLMODE=require
```

Then run `python -m db.migrate` once if the database is new. Never run
`pytest` with `DATABASE_URL`/`DB_HOST` exported in that shell.

**Container / ECS.** The image already sets the container defaults
(below). The task definition adds the database and S3 settings; see
[AWS: ECS task definition](#aws-ecs-task-definition).

## Reference

### Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | *(unset)* | Full SQLAlchemy URL. **Wins over every `DB_*` variable.** A bare `postgresql://` or `postgres://` is pinned to the psycopg 3 driver. |
| `DB_HOST` | *(unset)* | Used only when `DATABASE_URL` is unset. Setting it switches to Postgres. |
| `DB_PORT` | `5432` | |
| `DB_NAME` | `recon` | Use a dedicated database, not the server's built-in `postgres`. |
| `DB_USER` | *(unset)* | **Secret on AWS**: comes from Secrets Manager. |
| `DB_PASSWORD` | *(unset)* | **Secret.** Any characters are safe. In `backend/.env` wrap it in single quotes. |
| `DB_SSLMODE` | *(driver default: `prefer`)* | `require` for RDS. `verify-full` also needs the RDS CA bundle. |
| `RUN_MIGRATIONS_ON_STARTUP` | `true` (image: `false`) | Run alembic migrations and seeding when the API starts. Deployments set `false` and run `python -m db.migrate` once per release. On Postgres, concurrent runs are serialised by an advisory lock. |

With neither `DATABASE_URL` nor `DB_HOST`: SQLite at `RECON_DATA_DIR/app.db`,
plus sibling `bronze.db` / `silver.db` / `gold.db`.

### File storage

Uploaded source files ("bronze") and run workbooks. The database stores a
*reference* to each file: a local path or an `s3://` URI. Reads follow the
reference, so rows written under either backend stay readable after switching.

| Variable | Default | Notes |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local`: under `RECON_DATA_DIR`. `s3`: the buckets below. |
| `S3_BRONZE_BUCKET` | *(unset)* | Required for `s3`. Uploaded source files: `{prefix}bronze/{customer}/{sha256}.{ext}`. Content-addressed, so identical bytes are stored once. |
| `S3_RUNS_BUCKET` | = `S3_BRONZE_BUCKET` | Run workbooks: `{prefix}runs/{run_id}/Recon_Output.xlsx`. |
| `S3_PREFIX` | *(none)* | Optional key prefix, e.g. `recon`. |
| `S3_KMS_KEY_ID` | *(unset)* | Request SSE-KMS with this key on every upload. Unset = rely on the bucket's default encryption. |
| `AWS_REGION` | *(unset; image: `ap-south-1`)* | Region for the S3 client. |
| `S3_ENDPOINT_URL` | *(unset)* | Local S3 stand-ins only (MinIO, moto server). **Never set on AWS.** |

Credentials are **not** settings. On ECS they come from the task role. On a
laptop the standard AWS chain applies: `AWS_PROFILE`, or `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY`.

Retention: with `local`, only the newest 20 run workbooks are kept. With `s3`
nothing is deleted by the app. Add an S3 lifecycle rule on `{prefix}runs/`.

### Logging

| Variable | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, ... |
| `LOG_FORMAT` | `text` (image: `json`) | Console output. `json` writes one JSON object per line with `request_id`, `customer_id`, `run_id`, `event_type`, `details`: queryable in CloudWatch Logs Insights. |
| `LOG_TO_FILE` | `true` (image: `false`) | Rotating JSON file at `RECON_LOG_DIR/app.log`. Off in containers: their disk is ephemeral and stdout already reaches CloudWatch. |
| `RECON_LOG_DIR` | `backend/data/logs` | Only used when `LOG_TO_FILE` is on. |

### App

| Variable | Default | Notes |
|---|---|---|
| `RECON_DATA_DIR` | `backend/data` (image: `/tmp/recon-data`) | Local storage root and SQLite location. In a container it is scratch space only. |
| `FRONTEND_DIST` | `frontend/dist` (image: `/app/frontend_dist`) | Built React app served at `/`. No `index.html` there = API only, which is what local development with Vite on :5173 wants. |

### Sign-in

Every `/api` route except `/api/health` and `/api/auth/*` requires a session
cookie. See [`backend/app/auth.py`](../backend/app/auth.py).

| Variable | Default | Notes |
|---|---|---|
| `SESSION_SECRET` | *(random per process)* | Signs the session cookie. **Set this in production**: without it every restart signs everyone out, and two containers issue cookies the other rejects. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `SESSION_MAX_AGE` | `28800` (8h) | Cookie lifetime in seconds. |
| `COOKIE_SECURE` | `false` | `true` marks the cookie HTTPS-only — set it behind the ALB, leave it off for plain-http localhost. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | *(unset)* | Creates the **first** login on startup, and only while the `users` table is empty. Never re-passwords an existing account, so rotating them does nothing — that is deliberate. |

Accounts after the first are made with the CLI, which never takes a password
as an argument (argv is visible in `ps`):

```bash
cd backend && ../.venv/bin/python scripts/create_user.py -e person@example.com -n "Their Name"
../.venv/bin/python scripts/create_user.py --list
../.venv/bin/python scripts/create_user.py -e person@example.com --deactivate
```

Deactivating takes effect on that user's **next request** — the gate reloads
the row every time, which is the only way to withdraw a stateless cookie.

## Container defaults

Set in the [`Dockerfile`](../Dockerfile). Any of them can be overridden with `-e` or by the task definition.

```
FRONTEND_DIST=/app/frontend_dist   RECON_DATA_DIR=/tmp/recon-data
RUN_MIGRATIONS_ON_STARTUP=false    LOG_TO_FILE=false   LOG_FORMAT=json
AWS_REGION=ap-south-1
```

The container listens on **8080** as uid 10001 (non-root). The health check path is **`/api/health`**.

## AWS: ECS task definition

Plain `environment` values:

| Name | Example |
|---|---|
| `DB_HOST` | `wabtec-recon-dev-db.xxxx.ap-south-1.rds.amazonaws.com` |
| `DB_PORT` | `5432` |
| `DB_NAME` | `recon` |
| `DB_SSLMODE` | `require` |
| `STORAGE_BACKEND` | `s3` |
| `S3_BRONZE_BUCKET` | `wabtec-recon-documents` |
| `S3_RUNS_BUCKET` | `wabtec-recon-exports` |
| `S3_PREFIX` | `recon` |

`secrets`: values injected from Secrets Manager. The ARN suffix
`:username::` selects one JSON key of the secret:

```json
"secrets": [
  { "name": "DB_USER",     "valueFrom": "arn:aws:secretsmanager:ap-south-1:<account>:secret:wabtec/db-credentials-<suffix>:username::" },
  { "name": "DB_PASSWORD", "valueFrom": "arn:aws:secretsmanager:ap-south-1:<account>:secret:wabtec/db-credentials-<suffix>:password::" },
  { "name": "SESSION_SECRET", "valueFrom": "arn:aws:secretsmanager:ap-south-1:<account>:secret:wabtec/session-secret-<suffix>" }
]
```

`SESSION_SECRET` belongs in Secrets Manager, not in `environment`: anyone who
can read the task definition could otherwise mint a valid session cookie.
Set `COOKIE_SECURE=true` as a plain environment value at the same time.

Other task definition settings:

- **Migrations.** Run a one-off task from the same task definition, with the
  command overridden to `["python", "-m", "db.migrate"]`, before updating the
  service. A failure exits non-zero.
- **Logs.** `awslogs` driver, log group `/wabtec/recon-engine`.
- **Load balancer.** Target group: HTTP, port 8080, target type `ip`, health
  check `/api/health`. Set the ALB idle timeout to 300s. The image's uvicorn
  keep-alive is 310s, deliberately longer, to avoid 502s on reused connections.

## AWS: IAM permissions

**Task execution role** (used by ECS itself, before the app starts):

- `AmazonECSTaskExecutionRolePolicy`
- `secretsmanager:GetSecretValue` on the DB secret
- `kms:Decrypt` on the key encrypting that secret

**Task role** (used by the app):

| Action | Resource |
|---|---|
| `s3:GetObject`, `s3:PutObject` | `arn:aws:s3:::wabtec-recon-documents/*`, `arn:aws:s3:::wabtec-recon-exports/*` |
| `s3:ListBucket` | `arn:aws:s3:::wabtec-recon-documents`, `arn:aws:s3:::wabtec-recon-exports` |
| `kms:GenerateDataKey`, `kms:Decrypt` | the bucket encryption key |

> **`s3:ListBucket` is required, not optional.** Without it S3 answers a
> lookup of a missing object with 403 instead of 404. The app then cannot tell
> "new file" from "forbidden", and every new upload fails.

## CI / ECR

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on pushes to
`main` and `feat/aws-deployment`, on pull requests, and on demand.

| Job | What it does | Needs AWS? |
|---|---|---|
| `test` | Backend suite on SQLite and on Postgres 17. The golden-master tests that need the real sample documents are deselected by name; they still run locally. | No |
| `image` | Builds the image. Checks that no data files or `.env` are inside and that it runs as uid 10001. Runs `db.migrate` in the container against Postgres. Starts the container and checks the UI, the assets' cache header, the API and the JSON logs. | No |
| `push` | Pushes `<registry>/wabtec/recon-engine:<commit sha>` to ECR. | Yes |

`push` stays skipped until these **repository variables** exist
(GitHub → Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
|---|---|
| `AWS_ROLE_ARN` | ARN of the IAM role below (required) |
| `AWS_REGION` | `ap-south-1` (optional, default) |
| `ECR_REPOSITORY` | `wabtec/recon-engine` (optional, default) |

No AWS keys are stored in GitHub: the job exchanges a short-lived GitHub OIDC
token for the role. The DevOps setup is:

1. **Identity provider.** In the Wabtec account, add
   `token.actions.githubusercontent.com` (audience `sts.amazonaws.com`),
   if it is not there already.
2. **Role trust policy.** Only this repository's two branches may assume the role:

   ```json
   {
     "Effect": "Allow",
     "Principal": { "Federated": "arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com" },
     "Action": "sts:AssumeRoleWithWebIdentity",
     "Condition": {
       "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
       "StringLike": { "token.actions.githubusercontent.com:sub": [
         "repo:alokraj063/recon_engine:ref:refs/heads/main",
         "repo:alokraj063/recon_engine:ref:refs/heads/feat/aws-deployment"
       ] }
     }
   }
   ```

3. **Role permissions.**
   - `ecr:GetAuthorizationToken` on `*`
   - on the `wabtec/recon-engine` repository only:
     `ecr:BatchCheckLayerAvailability`, `ecr:InitiateLayerUpload`,
     `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage`,
     `ecr:BatchGetImage`

Images are tagged with the full commit SHA only. The task definition then
names an exact build, and pushes work even if the ECR repository has
immutable tags.
