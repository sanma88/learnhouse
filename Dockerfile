# ───────────────────────────────────────────────
# Stage 1: Frontend dependency install
# ───────────────────────────────────────────────
# HI-HA: bun reste épinglé exact (jamais de tag flottant `1-alpine`, qui a déjà
# cassé le build le 2026-08-20). Le motif du pin s'est inversé au merge 1.3.5 :
# le bun.lock régénéré est en lockfileVersion 3, que bun <= 1.3.14 ne sait pas
# parser (UnknownLockfileVersion) — 1.4.0 est la version prouvée en local
# (frozen install + build complets verts).
FROM oven/bun:1.4.0-alpine AS frontend-deps
RUN apk update && apk add --no-cache libc6-compat && rm -rf /var/cache/apk/*
WORKDIR /app

COPY apps/web/package.json apps/web/bun.lock* ./
RUN bun install --frozen-lockfile

# ───────────────────────────────────────────────
# Stage 2: Frontend build
# ───────────────────────────────────────────────
FROM oven/bun:1.4.0-alpine AS frontend-builder
# HI-HA: `next build` doit tourner sous Node reel, pas sous le runtime Bun.
# L'image oven/bun place un shim node->bun (/usr/local/bun-node-fallback-bin,
# dernier du PATH) : sans vrai node, le shebang `#!/usr/bin/env node` de
# node_modules/.bin/next retombe sur Bun, et Bun 1.3.14 musl segfaultait au
# teardown de next build 16.3.4 (CI #604, exit 139 apres compilation reussie).
# apk nodejs (Alpine 3.22) = Node 22, meme major que le runtime nodesource du
# stage final ; /usr/bin/node precede le shim dans le PATH, donc `bun run build`
# execute next sous Node sans autre changement. Bun reste le runtime d'install.
RUN apk add --no-cache nodejs
WORKDIR /app
COPY --from=frontend-deps /app/node_modules ./node_modules
COPY apps/web .

# Disable telemetry during build
ENV NEXT_TELEMETRY_DISABLED=1

# Remove .env files to avoid leaking secrets into the build
RUN rm -f .env*

RUN bun run build

# ───────────────────────────────────────────────
# Stage 3: Frontend production image
# ───────────────────────────────────────────────
FROM oven/bun:1.4.0-alpine AS frontend-runner
WORKDIR /app

RUN apk update && apk add --no-cache curl && rm -rf /var/cache/apk/*

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=frontend-builder /app/public ./public

RUN mkdir .next && chown nextjs:nodejs .next

# Leverage output traces to reduce image size
COPY --from=frontend-builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=frontend-builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Copy server wrapper for runtime environment variable injection
COPY --chown=nextjs:nodejs apps/web/server-wrapper.js ./
RUN chmod +x server-wrapper.js

# ───────────────────────────────────────────────
# Stage 4: Collab server build
# ───────────────────────────────────────────────
FROM oven/bun:1.4.0-alpine AS collab-builder
WORKDIR /app

COPY apps/collab/package.json apps/collab/bun.lock* ./
RUN bun install --frozen-lockfile

COPY apps/collab/tsconfig.json ./
COPY apps/collab/src/ ./src/

RUN bun run build

# ───────────────────────────────────────────────
# Stage 5: Final image combining frontend + backend + collab
# ───────────────────────────────────────────────
FROM python:3.14.6-slim-bookworm AS runner

# Single apt layer: nginx, curl, netcat, node, pm2
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx curl netcat-openbsd ca-certificates gnupg unzip build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g pm2 \
    && curl -fsSL https://bun.sh/install | bash \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /root/.npm \
    && rm /etc/nginx/sites-enabled/default

ENV PATH="/root/.bun/bin:${PATH}"

# Copy the frontend standalone build
COPY --from=frontend-runner /app /app/web

# Backend: install deps first (better layer caching)
WORKDIR /app/api
COPY ./apps/api/uv.lock ./apps/api/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip uv \
    && uv sync --no-dev
COPY ./apps/api ./

# Remove Enterprise Edition folder for public builds
ARG LEARNHOUSE_PUBLIC=false
RUN if [ "$LEARNHOUSE_PUBLIC" = "true" ]; then rm -rf /app/api/ee; fi

# Collab server: copy built JS + production deps
WORKDIR /app/collab
COPY --from=collab-builder /app/dist ./dist
COPY apps/collab/package.json apps/collab/bun.lock* ./
RUN bun install --production

# Copy configs and scripts
WORKDIR /app
COPY ./docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY ./apps/api/docker-entrypoint.sh /app/api/docker-entrypoint.sh
COPY ./docker/start.sh /app/start.sh
RUN chmod +x /app/api/docker-entrypoint.sh /app/start.sh

# PYTHONDONTWRITEBYTECODE: the image ships read-only source and gains nothing
# from writing .pyc files back into it. It also keeps __pycache__ out of the
# enterprise tree, where stale bytecode could otherwise shadow a source file
# that verifies clean against the signed manifest.
ENV PORT=8000 LEARNHOUSE_PORT=9000 COLLAB_PORT=4000 HOSTNAME=0.0.0.0 LEARNHOUSE_OSS=true NEXT_PUBLIC_LEARNHOUSE_OSS=true PYTHONDONTWRITEBYTECODE=1

# HI-HA: the image and its source live in different places — one registry, one
# git mirror, no link between them but this label. `image.source` is the only
# thing carrying the route back, readable with `docker inspect` by anyone who
# ends up holding the image without knowing where it came from.
LABEL org.opencontainers.image.source="https://github.com/sanma88/learnhouse" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.description="HI-HA Campus — fork AGPL-3.0 de LearnHouse"

EXPOSE 80 9000 4000

# HI-HA: the image ships no healthcheck, so an orchestrator has to invent one —
# Coolify's default reaches for `wget`, which this image does not carry, and the
# container never leaves "starting". curl is installed above. 127.0.0.1 rather
# than localhost for the same reason as nginx.conf: on an IPv6-enabled host the
# name resolves to ::1 first and nothing listens there.
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=120s \
    CMD curl -fsS http://127.0.0.1:80/api/v1/health || exit 1

CMD ["sh", "/app/start.sh"]
