# syntax=docker/dockerfile:1
# ParallelVision - container image (Assignment 2, Part I)
#
# Two stages ("multi-stage build"):
#   1. builder : has a C++ compiler, builds the OpenMP filter library
#   2. runtime : small Python image with only what is needed to RUN the app
# The compiler never reaches the final image, which keeps it small.

############################ stage 1: builder ############################
FROM python:3.12-slim AS builder

RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY cpp/filters.cpp .
RUN g++ -O3 -fopenmp -shared -fPIC -o libfilters.so filters.cpp -lm

############################ stage 2: runtime ############################
FROM python:3.12-slim

LABEL org.opencontainers.image.title="ParallelVision" \
      org.opencontainers.image.description="Parallel image-processing web app (Flask + gunicorn + C++/OpenMP)" \
      org.opencontainers.image.source="https://github.com/ramishrana52843/parallelvision-docker"

# PYTHONUNBUFFERED: logs appear immediately in `docker logs`
# PYTHONDONTWRITEBYTECODE: do not create any .pyc files inside the container
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp1 = the OpenMP runtime that libfilters.so needs at run time
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# Run as a normal user, not as root
RUN useradd --create-home --uid 1000 app

WORKDIR /app

# Copy ONLY requirements.txt first: Docker caches this layer, so the (slow)
# pip install is skipped on later builds unless requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# The compiled C++ library comes from stage 1; the app finds it via FILTERS_LIB
COPY --from=builder /build/libfilters.so /usr/local/lib/libfilters.so
ENV FILTERS_LIB=/usr/local/lib/libfilters.so

# Now the application code (what is excluded is listed in .dockerignore)
COPY . .

USER app
EXPOSE 8000

# Docker marks the container "healthy" only when /health answers
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

# exec form: gunicorn is PID 1 and receives stop signals correctly
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "application:application"]
