// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const pino = require('pino');
const { trace } = require('@opentelemetry/api');

const logger = pino({
  transport: {
    target: 'pino-opentelemetry-transport',
    options: {
      messageKey: 'message'
    }
  }
});

module.exports = logger;
