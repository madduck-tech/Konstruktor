# ==============================================================================
# Konstruktor runner - Go variant
# ==============================================================================
# docker build -f deploy/docker/Dockerfile.go -t konstruktor-runner:go .
# ==============================================================================

FROM konstruktor-runner:base

LABEL org.konstruktor.runner="go"

ENV GO_VERSION=1.24.5

RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" \
    | tar -C /usr/local -xz \
    && ln -s /usr/local/go/bin/go /usr/local/bin/go \
    && ln -s /usr/local/go/bin/gofmt /usr/local/bin/gofmt
