# syntax=docker/dockerfile:1.7
FROM node:22-slim AS base
WORKDIR /app

# The production image is intentionally install-agnostic. Supabase URL/key and
# API origin are supplied at runtime: server-side through container env and
# browser-side through same-origin + a public config cookie set by proxy.ts.
# Google auth remains disabled in the single-user prebuilt image by default.
ENV NEXT_PUBLIC_GOOGLE_AUTH_ENABLED=false

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

RUN useradd -m app && chown -R app:app /app
USER app

EXPOSE 3000
CMD ["npm", "start"]
