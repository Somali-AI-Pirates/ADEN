# Static export artifact consumed by the host Nginx release process.
FROM node:22.21.1-alpine3.22@sha256:b2358485e3e33bc3a33114d2b1bdb18cdbe4df01bd2b257198eb51beb1f026c5 AS build
WORKDIR /web-build
COPY web/package.json web/package-lock.json ./
COPY web/scripts ./scripts
RUN npm ci
COPY web/ ./
RUN npm run build

FROM scratch AS export
COPY --from=build /web-build/out/ /web/
