# syntax=docker/dockerfile:1
#
# OpsPilot frontend image.
#
# Build context: ./frontend (see compose.yaml).
#
# IMPORTANT: NEXT_PUBLIC_* values are compiled into the browser bundle by
# `next build`. They are build arguments, not runtime settings. Changing the
# API URL or OIDC configuration requires rebuilding this image.

FROM node:24.22-bookworm-slim AS deps

WORKDIR /app

COPY package.json package-lock.json ./

RUN npm ci --no-audit --no-fund


FROM node:24.22-bookworm-slim AS builder

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_STANDALONE=1

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Public, browser-visible build-time configuration. Never pass secrets here:
# every value below ends up inside the client bundle.
ARG NEXT_PUBLIC_OPSPILOT_API_URL=http://127.0.0.1:8000
ARG NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY=
ARG NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID=
ARG NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI=
ARG NEXT_PUBLIC_OPSPILOT_OIDC_SCOPE=openid profile email
ARG NEXT_PUBLIC_OPSPILOT_ROLES_CLAIM=roles
ARG NEXT_PUBLIC_OPSPILOT_DEV_AUTH=false

ENV NEXT_PUBLIC_OPSPILOT_API_URL=$NEXT_PUBLIC_OPSPILOT_API_URL \
    NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY=$NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY \
    NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID=$NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID \
    NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI=$NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI \
    NEXT_PUBLIC_OPSPILOT_OIDC_SCOPE=$NEXT_PUBLIC_OPSPILOT_OIDC_SCOPE \
    NEXT_PUBLIC_OPSPILOT_ROLES_CLAIM=$NEXT_PUBLIC_OPSPILOT_ROLES_CLAIM \
    NEXT_PUBLIC_OPSPILOT_DEV_AUTH=$NEXT_PUBLIC_OPSPILOT_DEV_AUTH

RUN npm run build


FROM node:24.22-bookworm-slim AS runner

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# The `node` user (uid 1000) ships with the official image; /app is owned by it
# so the standalone server can write its runtime cache.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

USER node

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then((r) => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "server.js"]
