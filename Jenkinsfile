// Jenkinsfile - Assignment 2, Part II: containerized automation pipeline
//
// Plugins used:  Git (checkout)  +  Pipeline (this file)  +  Docker Pipeline (docker.image().inside)
// Trigger:       GitHub webhook  (configured in the job: "GitHub hook trigger for GITScm polling")
//
// What a build does:
//   1. fetches the code from GitHub
//   2. checks the code inside a throw-away Python container
//   3. starts the database + web containers with docker-compose.ci.yml
//      (the code is attached to the web container as a volume)
//   4. proves the stack works: health check, then the tables in the MySQL container
//
// Secrets are NOT in this file. They come from Jenkins credentials (IDs below).

pipeline {
    agent any

    options {
        skipDefaultCheckout(true)               // we do the checkout ourselves in stage 1
        timestamps()                            // time stamp on every console line
        disableConcurrentBuilds()               // two pushes must not run two builds at once
        timeout(time: 25, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }

    parameters {
        booleanParam(name: 'TEARDOWN', defaultValue: false,
                     description: 'Stop and remove the CI containers at the end (the database volume is kept)')
    }

    environment {
        COMPOSE_FILE = 'docker-compose.ci.yml'  // so plain "docker compose ..." uses this file
        // S3_BUCKET / AWS_REGION: Manage Jenkins > System > Global properties > Environment variables
        S3_BUCKET    = "${env.S3_BUCKET ?: ''}"
        AWS_REGION   = "${env.AWS_REGION ?: 'eu-north-1'}"
    }

    stages {

        stage('Checkout from GitHub') {
            steps {
                checkout scm                    // Git plugin: clones the repo + branch of this job
                sh 'git log -1 --pretty=format:"commit %h by %an: %s"'
            }
        }

        stage('Check Docker') {
            steps {
                sh '''
                    docker version --format 'server {{.Server.Version}}'
                    docker compose version
                '''
            }
        }

        stage('Static checks in a Python container') {
            steps {
                script {
                    // Docker Pipeline plugin: run the commands INSIDE a container.
                    // The workspace is mounted automatically; HOME=/tmp lets pip --user work.
                    docker.image('python:3.12').inside('-e HOME=/tmp -e PIP_DISABLE_PIP_VERSION_CHECK=1') {
                        sh '''
                            python --version
                            pip install --user --quiet jinja2
                            python -c "import ast,sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]; print('python syntax ok')" \
                                application.py auth.py config.py mailer.py models.py processor.py storage.py
                            python ci/check_templates.py
                            g++ -O2 -fopenmp -shared -fPIC -o /tmp/check.so cpp/filters.cpp -lm
                            echo "C++ library compiles"
                        '''
                    }
                }
            }
        }

        stage('Start stack with docker compose') {
            steps {
                withCredentials([
                    string(credentialsId: 'pv-secret-key',         variable: 'SECRET_KEY'),
                    string(credentialsId: 'pv-db-password',        variable: 'MYSQL_PASSWORD'),
                    string(credentialsId: 'pv-db-root-password',   variable: 'MYSQL_ROOT_PASSWORD')
                ]) {
                    sh '''
                        docker compose config -q
                        echo "--- database container (kept running, data lives in a volume)"
                        docker compose up -d --wait db
                        echo "--- web container (recreated every build so it runs the NEW code)"
                        docker compose up -d --force-recreate --no-deps web
                    '''
                }
            }
        }

        stage('Smoke test') {
            steps {
                sh '''
                    for i in $(seq 1 60); do
                        if curl -fsS http://localhost:8081/health | grep -q '"status":"ok"'; then
                            echo "application is healthy (attempt $i)"
                            exit 0
                        fi
                        sleep 3
                    done
                    echo "application did not become healthy"
                    docker logs --tail 60 pvci_web
                    exit 1
                '''
            }
        }

        stage('Database check') {
            steps {
                // the password variable is read INSIDE the db container, not by Jenkins
                sh '''
                    docker exec pvci_db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SHOW TABLES;" "$MYSQL_DATABASE"'
                '''
            }
        }

        stage('Teardown (optional)') {
            when { expression { return params.TEARDOWN } }
            steps {
                withCredentials([
                    string(credentialsId: 'pv-secret-key',         variable: 'SECRET_KEY'),
                    string(credentialsId: 'pv-db-password',        variable: 'MYSQL_PASSWORD'),
                    string(credentialsId: 'pv-db-root-password',   variable: 'MYSQL_ROOT_PASSWORD')
                ]) {
                    sh 'docker compose down'
                }
            }
        }
    }

    post {
        always {
            sh 'docker ps --filter "name=pvci_" --format "table {{.Names}}\\t{{.Status}}\\t{{.Ports}}" || true'
        }
        success {
            echo 'BUILD OK - the web app is running at http://<server-ip>:8081/'
        }
        failure {
            sh 'docker logs --tail 80 pvci_web || true'
            echo 'BUILD FAILED - read the stage that is red, and the container log above.'
        }
    }
}
