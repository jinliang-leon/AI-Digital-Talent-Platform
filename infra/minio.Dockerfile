FROM golang:1.24-bookworm AS build
ENV CGO_ENABLED=0 GOBIN=/out
# Upstream MinIO RELEASE.2025-04-22T22-12-26Z, resolved to the release commit.
RUN go install -trimpath github.com/minio/minio@0d7408fc9969caf07de6a8c3a84f9fbb10a6739e

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o 10001 -g 10001 /data /home/minio
COPY --from=build /out/minio /usr/local/bin/minio
ENV HOME=/home/minio
USER 10001:10001
ENTRYPOINT ["minio"]
