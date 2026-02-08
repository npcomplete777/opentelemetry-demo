// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const health = require('grpc-js-health-check');
const opentelemetry = require('@opentelemetry/api');

const search = require('./search');
const logger = require('./logger');

async function searchProductsHandler(call, callback) {
  const span = opentelemetry.trace.getActiveSpan();

  try {
    logger.info({ request: call.request }, 'SearchProducts request received');

    const response = await search.searchProducts(call.request);
    callback(null, response);

  } catch (err) {
    logger.warn({ err });

    span?.recordException(err);
    span?.setStatus({ code: opentelemetry.SpanStatusCode.ERROR });
    callback(err);
  }
}

async function getSuggestionsHandler(call, callback) {
  const span = opentelemetry.trace.getActiveSpan();

  try {
    logger.info({ request: call.request }, 'GetSuggestions request received');

    const response = await search.getSuggestions(call.request);
    callback(null, response);

  } catch (err) {
    logger.warn({ err });

    span?.recordException(err);
    span?.setStatus({ code: opentelemetry.SpanStatusCode.ERROR });
    callback(err);
  }
}

async function closeGracefully(signal) {
  server.forceShutdown();
  process.kill(process.pid, signal);
}

const otelDemoPackage = grpc.loadPackageDefinition(protoLoader.loadSync('demo.proto'));
const server = new grpc.Server();

server.addService(health.service, new health.Implementation({
  '': health.servingStatus.SERVING
}));

server.addService(otelDemoPackage.oteldemo.SearchService.service, {
  SearchProducts: searchProductsHandler,
  GetSuggestions: getSuggestionsHandler
});

let ip = "0.0.0.0";

const ipv6_enabled = process.env.IPV6_ENABLED;

if (ipv6_enabled == "true") {
  ip = "[::]";
  logger.info(`Overwriting Localhost IP: ${ip}`);
}

const address = ip + `:${process.env['SEARCH_PORT']}`;

server.bindAsync(address, grpc.ServerCredentials.createInsecure(), (err, port) => {
  if (err) {
    return logger.error({ err });
  }

  logger.info(`search gRPC server started on ${address}`);
});

process.once('SIGINT', closeGracefully);
process.once('SIGTERM', closeGracefully);
