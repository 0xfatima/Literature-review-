FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/web/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else echo 'WARNING: no package-lock.json - floating dependency versions' >&2; npm install; fi

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web ./
ARG API_BASE_URL=http://api:8000
ENV API_BASE_URL=$API_BASE_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S app && adduser -S app -G app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER app
EXPOSE 3000
CMD ["node", "server.js"]
