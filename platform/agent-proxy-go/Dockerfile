# ─── Stage 1: Build ───────────────────────────────────────────────
FROM golang:1.22-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /src

# Cache dependencies
COPY go.mod go.sum ./
RUN go mod download

# Build static binary
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build \
    -ldflags="-s -w" \
    -o /agent-proxy \
    ./cmd/proxy

# ─── Stage 2: Runtime (minimal Alpine for curl healthcheck) ──────
FROM alpine:3.19

RUN apk add --no-cache ca-certificates curl \
    && adduser -D -u 1000 ccos

COPY --from=builder /agent-proxy /usr/local/bin/agent-proxy

# Data directory for audit log
RUN mkdir -p /data && chown ccos:ccos /data
VOLUME ["/data"]

EXPOSE 8400

USER ccos

HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=5s \
    CMD ["curl", "-sf", "http://localhost:8400/healthz"]

ENTRYPOINT ["agent-proxy"]
