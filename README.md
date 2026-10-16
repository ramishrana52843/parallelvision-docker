# ParallelVision - containerized (Assignment 2)

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

## Part II quick start

Jenkins builds from GitHub using the `Jenkinsfile`; the CI stack answers on `http://SERVER_IP:8081/`.
See the step-by-step guide for the Jenkins installation, credentials and webhook.

| | Part I | Part II |
|---|---|---|
| Containers | `pv_web`, `pv_db` | `pvci_web`, `pvci_db` |
| Website port | 80 | 8081 |
| Database port (localhost only) | 3306 | 3307 |
| Database volume | `pv_mysql_data` | `pvci_mysql_data` |
| App code comes from | the Docker image | a volume (the Jenkins workspace) |
