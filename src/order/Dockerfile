# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM golang:1.24-bookworm AS builder

WORKDIR /usr/src/app/

COPY ./src/order/go.mod go.mod
COPY ./src/order/go.sum go.sum

RUN go mod download

COPY ./src/order/genproto/oteldemo/ genproto/oteldemo/
COPY ./src/order/main.go main.go

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags "-s -w" -o order main.go

FROM gcr.io/distroless/static-debian12:nonroot

WORKDIR /usr/src/app/

COPY --from=builder /usr/src/app/order/ ./

EXPOSE ${ORDER_PORT}
ENTRYPOINT [ "./order" ]
