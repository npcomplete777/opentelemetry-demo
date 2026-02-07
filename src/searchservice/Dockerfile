# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM docker.io/library/node:22-slim AS builder

WORKDIR /usr/src/app/

COPY ./src/searchservice/package.json package.json

RUN npm install --omit=dev

# -----------------------------------------------------------------------------

FROM gcr.io/distroless/nodejs22-debian12:nonroot

WORKDIR /usr/src/app/

COPY --from=builder /usr/src/app/node_modules/ node_modules/

COPY ./pb/demo.proto demo.proto

COPY ./src/searchservice/search.js search.js
COPY ./src/searchservice/index.js index.js
COPY ./src/searchservice/logger.js logger.js
COPY ./src/searchservice/opentelemetry.js opentelemetry.js

EXPOSE ${SEARCH_PORT}

CMD ["--require=./opentelemetry.js", "index.js"]
