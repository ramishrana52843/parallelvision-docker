# ParallelVision  (Assignment 2 CI)

Flask + gunicorn image-processing app with a C++/OpenMP filter library, MySQL, and S3 storage.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the app image (multi-stage: compiles the C++ library, then a small runtime image) |
| `.dockerignore` | Files kept out of the image |
| `docker-compose.yml` | **Part I** - web + MySQL, MySQL data in a named volume, image pulled from Docker Hub |
| `docker-compose.ci.yml` | **Part II** - same stack, but the code is attached as a volume; other names and ports |
| `Jenkinsfile` | **Part II** - Jenkins pipeline (Git + Pipeline + Docker Pipeline plugins) |
| `ci/check_templates.py` | Checks the HTML templates in the pipeline |
| `.env.example` | Template for the secrets/settings file `.env` (never commit `.env`) |

## Part I quick start (on the EC2 server, Docker installed)

```bash
cp .env.example .env && nano .env                 # fill in the values
docker build -t YOUR_DOCKERHUB_USER/parallelvision:1.0 .
docker login -u YOUR_DOCKERHUB_USER               # password = a Docker Hub access token
docker push YOUR_DOCKERHUB_USER/parallelvision:1.0
docker compose up -d
docker compose ps
```
Open `http://SERVER_IP/`. Data check: `docker compose down` then `docker compose up -d` - your account is still there.

**Before you push to GitHub:** in `docker-compose.yml` replace `YOUR_DOCKERHUB_USER` with your Docker Hub username (the image name must match the one on Docker Hub).

## Part II quick start

Jenkins builds from GitHub using the `Jenkinsfile`; the CI stack answers on `http://SERVER_IP:8081/`.
See the step-by-step guide for the Jenkins installation, credentials and webhook.

| | Part I | Part II |
|---|---|---|
| Containers | `pv_web`, `pv_db` | `pvci_web`, `pvci_db` |
| Website port (host : container) | 80 : 8000 | 8081 : 8000 |
| Database port (host : container) | 3308 : 3306 | 3307 : 3306 |
| Database volume | `pv_mysql_data` | `pvci_mysql_data` |
| App code comes from | the Docker image | a volume (the Jenkins workspace) |

## What the Jenkins pipeline e-mails

After every build `sendBuildEmail()` (end of the `Jenkinsfile`) e-mails the result to the author of the pushed commit.
Needs: SMTP set up in Manage Jenkins > System > E-mail Notification, and optionally the global properties
`FALLBACK_EMAIL` (used when the commit address is a GitHub noreply address) and `OWNER_EMAIL` (gets a copy).
