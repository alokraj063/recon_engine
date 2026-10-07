# Pushing the image from AWS CloudShell

How to build the Recon Alpha image and push it to ECR by hand, from AWS
CloudShell, without GitHub Actions. Use this until the CI `push` job is
switched on (see [configuration.md → CI / ECR](configuration.md#ci--ecr)).

| | |
|---|---|
| Account | `099731417215` |
| Region | `ap-south-1` (Mumbai) |
| ECR repository | `wabtec/recon-engine` |
| Registry | `099731417215.dkr.ecr.ap-south-1.amazonaws.com` |
| Branch deployed to DEV | `dev` |

CloudShell already has `git`, `docker` and the AWS CLI, and is signed in as
the console user, so no access keys are needed. The console user needs the
ECR push permissions listed in configuration.md.

---

## 1. Open CloudShell

AWS console → switch the region (top right) to **Asia Pacific (Mumbai)** →
the **CloudShell** icon (`>_`) in the top bar.

## 2. Get the code

**First time only** — clone the repository with all its branches:

```bash
git clone https://github.com/alokraj063/recon_engine.git
cd recon_engine
git checkout dev
```

**Every time after that:**

```bash
cd ~/recon_engine
git fetch
git checkout dev
git pull
```

Check you are on the commit you mean to deploy:

```bash
git log -1 --oneline
```

## 3. Build

Keep the `docker build` command on **one line** — split at a `\` when
pasted, it loses the trailing `.`, which is the build folder and required:

```bash
TAG=$(git rev-parse --short HEAD); echo "tag: $TAG"
docker build --platform linux/amd64 -t 099731417215.dkr.ecr.ap-south-1.amazonaws.com/wabtec/recon-engine:$TAG .
```

The tag is the short commit hash, so every image in ECR names the exact
commit it was built from. A rebuild of an unchanged commit reuses the cached
layers and finishes in seconds.

## 4. Log in to ECR and push

`$TAG` only lives in the current CloudShell session — if it timed out since
step 3, run the `TAG=…` line again first.

```bash
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 099731417215.dkr.ecr.ap-south-1.amazonaws.com
docker push 099731417215.dkr.ecr.ap-south-1.amazonaws.com/wabtec/recon-engine:$TAG
```

The login lasts 12 hours. Confirm the push landed:

```bash
aws ecr describe-images --repository-name wabtec/recon-engine --region ap-south-1 --query 'sort_by(imageDetails,&imagePushedAt)[-1].imageTags'
```

It should print the tag from step 3.

## 5. Deploy it

The rest runs on your own machine, from the repository root, with the
`deploy/deploy_dev.py` script (kept locally, not in git). It picks the
**newest** image in ECR by itself.

```powershell
.venv\Scripts\python.exe deploy\deploy_dev.py discover            # dry run: check the image tag it found
.venv\Scripts\python.exe deploy\deploy_dev.py migrate --apply     # only if the commit adds a migration
.venv\Scripts\python.exe deploy\deploy_dev.py deploy --apply
```

In the `discover` output, `image` must end in the tag you just pushed. Run
`migrate` **after** the push, never before: it runs the database migrations
of whichever image it finds, and an older image migrates to an older schema.

Then open <https://wabtec.reconalpha-staging.joulestowatts.com>. The login
page should load; `/api/health` answers without signing in.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error: pathspec 'dev' did not match any file(s) known to git` | The clone was made with `--single-branch`, so `git fetch` only updates that one branch. | `git fetch origin dev:dev && git checkout dev`. To see every branch from then on: `git remote set-branches origin '*' && git fetch`. |
| `"docker buildx build" requires exactly 1 argument` | The command was split at a `\` line break when pasted, and the trailing `.` was lost. | Paste the build command as a single line. |
| `no basic auth credentials` / `denied` on push | Not logged in to ECR, or the login expired. | Re-run the `aws ecr get-login-password …` line from step 4. |
| `bash: cd: recon_engine: No such file or directory` | You are already inside `recon_engine`. | Skip the `cd`. |
| `No space left on device` | CloudShell's disk is small and old images pile up. | `docker system prune -af` and rebuild. |
| Task starts in ECS, then exits with `ModuleNotFoundError` | A new top-level backend file was not let into the image. `.dockerignore` is an **allowlist** — anything not named is left out. | Add `!backend/<file>` to `.dockerignore`, commit, push the branch, rebuild. (This happened with `backend/passwords.py`.) |
| `discover` shows the old image tag | The push did not finish, or went to another region. | Check step 4's `describe-images` output. |
