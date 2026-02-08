# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# https://mcr.microsoft.com/v2/dotnet/sdk/tags/list
FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:10.0 AS builder
ARG TARGETARCH

WORKDIR /usr/src/app/

COPY ./src/user/ ./
COPY ./pb/ ./pb/

RUN dotnet restore ./src/user.csproj -r linux-musl-$TARGETARCH

RUN dotnet publish ./src/user.csproj -r linux-musl-$TARGETARCH --no-restore -o /user

# -----------------------------------------------------------------------------

# https://mcr.microsoft.com/v2/dotnet/runtime-deps/tags/list
FROM mcr.microsoft.com/dotnet/runtime-deps:10.0-alpine3.22

WORKDIR /usr/src/app/
COPY --from=builder /user/ ./

ENV DOTNET_HOSTBUILDER__RELOADCONFIGONCHANGE=false

EXPOSE ${USER_PORT}
ENTRYPOINT [ "./user" ]
