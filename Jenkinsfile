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
//   5. e-mails the build result to the person who made the push (see sendBuildEmail below)
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
        // Also global properties: FALLBACK_EMAIL (used if the commit address is unusable)
        // and OWNER_EMAIL (gets a copy of every result mail). Both are optional.
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
            script {
                sendBuildEmail()
            }
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

// ---------------------------------------------------------------------------
// E-mail the build result to the person who made the push.
//   * recipient = the author address of the commit that was just built
//   * if that address is missing or a GitHub "noreply" address, FALLBACK_EMAIL is used
//   * OWNER_EMAIL (optional) gets a copy
// SMTP is configured once in Manage Jenkins > System > E-mail Notification.
// A problem with e-mail never changes the build result.
// ---------------------------------------------------------------------------
def sendBuildEmail() {
    def author  = sh(script: 'git log -1 --pretty=format:%ae || true', returnStdout: true).trim()
    def pusher  = sh(script: 'git log -1 --pretty=format:%an || true', returnStdout: true).trim()
    def commit  = sh(script: 'git log -1 --pretty=format:%h || true',  returnStdout: true).trim()
    def message = sh(script: 'git log -1 --pretty=format:%s || true',  returnStdout: true).trim()

    def recipient = author
    if (!recipient || recipient.endsWith('noreply.github.com')) {
        recipient = env.FALLBACK_EMAIL ?: ''
    }
    if (!recipient) {
        echo 'E-mail skipped: no commit author address and FALLBACK_EMAIL is not set.'
        return
    }

    def result  = currentBuild.currentResult
    def subject = "[ParallelVision CI] ${result}: ${env.JOB_NAME} #${env.BUILD_NUMBER}"
    def body = """Build result: ${result}

Job:        ${env.JOB_NAME}
Build:      #${env.BUILD_NUMBER}
Commit:     ${commit} - ${message}
Pushed by:  ${pusher} <${author}>

Build page:     ${env.BUILD_URL}
Console output: ${env.BUILD_URL}console

On success the web application is running on port 8081 of the Jenkins server.
"""

    try {
        if (env.OWNER_EMAIL) {
            mail to: recipient, cc: env.OWNER_EMAIL, subject: subject, body: body
        } else {
            mail to: recipient, subject: subject, body: body
        }
        echo "Build result e-mailed to ${recipient}"
    } catch (err) {
        echo "Could not send the e-mail: ${err}"
    }
}
