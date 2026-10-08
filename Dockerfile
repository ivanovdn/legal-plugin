# Pinned — tag for people, digest for the bytes: the exact base the VM built
# with on 2026-10-08. A floating tag lets a rebuild change the interpreter and
# the OS under an unchanged commit; bump it on purpose, like the locks.
FROM python:3.12.15-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

WORKDIR /app

# No apt layer, on purpose. It used to install build-essential + git "for wheels
# lacking manylinux builds", but every pinned package ships a wheel for both
# x86_64 and aarch64, nothing at runtime calls git, and `apt-get install` on a
# cold cache re-resolved OS packages on every build — a review on 2026-10-08
# watched it upgrade liblzma5 inside the pinned base, the same drift the locks
# exist to stop. If a dependency ever needs a compiler, the build fails loudly
# here instead; add a builder stage then, not this layer back.

# Install Python deps first for better layer caching. The image installs the
# runtime LOCK — every package, transitive ones included, at the version the
# tests run on (scripts/check_locks.py) — never the ranges in
# requirements-runtime.txt: on 2026-10-08 a rebuild re-resolved those ranges to
# fastapi 0.143.0 while the tests ran on 0.136.1. --no-deps makes the lock the
# whole truth; `pip check` fails the build if it ever lacks something a pinned
# package needs.
COPY requirements-runtime.lock .
RUN pip install --no-cache-dir --no-deps -r requirements-runtime.lock && pip check

# Copy the backend runtime code (see .dockerignore for exclusions).
COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
