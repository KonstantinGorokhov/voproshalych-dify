FROM golang:1.24 AS builder

WORKDIR /src

COPY bots/max/go.mod /src/go.mod
COPY bots/max/go.sum /src/go.sum
COPY bots/max/*.go /src/

RUN go mod download
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/max-bot .

FROM debian:bookworm-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /out/max-bot /app/max-bot

CMD ["/app/max-bot"]
