# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM golang:1.24-bookworm AS builder

WORKDIR /usr/src/app/

COPY ./src/pricing/go.mod go.mod
COPY ./src/pricing/go.sum go.sum

RUN go mod download

COPY ./src/pricing/genproto/oteldemo/ genproto/oteldemo/
COPY ./src/pricing/main.go main.go

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags "-s -w" -o pricing main.go

FROM gcr.io/distroless/static-debian12:nonroot

WORKDIR /usr/src/app/

COPY --from=builder /usr/src/app/pricing/ ./

EXPOSE ${PRICING_PORT}
ENTRYPOINT [ "./pricing" ]
