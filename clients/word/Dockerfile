# Build the Word task-pane bundle, then serve it from Caddy.
#
# WHY THIS EXISTS. The pane used to be built by hand on the deploy host and
# bind-mounted into Caddy (`./clients/word/dist:/srv:ro`). That drifted
# silently and had no way not to: `dist/` is gitignored so `git pull` never
# updates it, `docker compose up --build` rebuilds only the backend image, and
# SRV-AGENT-01 has no npm at all. Result — the VM served a bundle built
# 2026-08-11 while its backend went on being redeployed for two more days, so
# apply-path guards that had shipped to main were simply absent in production
# with nothing reporting a mismatch.
#
# Building here makes the backend and the pane come from the same commit by
# construction, and removes the host's Node dependency entirely.

FROM node:20-alpine AS build
WORKDIR /app

# Manifests first: this layer caches until dependencies actually change, so an
# ordinary source edit rebuilds in seconds rather than re-running npm ci.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# `npm run build` is `tsc --noEmit && vite build`, so a type error fails the
# image instead of quietly shipping a broken pane.
#
# vite.config.ts only loads office-addin-dev-certs when command === "serve",
# which is what lets this run in a container with no cert tooling or sudo.
RUN npm run build

# Caddy serves the built bundle from /srv (see Caddyfile: `root * /srv`).
# The Caddyfile itself stays a bind mount so cert/hostname changes don't
# require an image rebuild.
FROM caddy:2
COPY --from=build /app/dist /srv
