# syntax=docker/dockerfile:1.7
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS builder
WORKDIR /app
ENV NEXT_PUBLIC_GOOGLE_AUTH_ENABLED=false
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_PUBLIC_GOOGLE_AUTH_ENABLED=false \
    HOSTNAME=0.0.0.0 \
    PORT=3000

# node:22-slim already provides the unprivileged `node` user (uid 1000).
# Reuse it instead of creating another uid 1000 account.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

USER node
EXPOSE 3000
CMD ["node", "server.js"]
