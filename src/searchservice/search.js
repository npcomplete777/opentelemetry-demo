// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const { trace, SpanStatusCode } = require('@opentelemetry/api');
const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const { OpenFeature } = require('@openfeature/server-sdk');
const { FlagdProvider } = require('@openfeature/flagd-provider');
const flagProvider = new FlagdProvider();

const logger = require('./logger');
const tracer = trace.getTracer('search');

// In-memory cache for search results (product_id -> product data)
const productCache = new Map();

// Load ProductCatalogService client
const PROTO_PATH = 'demo.proto';
const packageDefinition = protoLoader.loadSync(PROTO_PATH, {
  keepCase: true,
  longs: String,
  enums: String,
  defaults: true,
  oneofs: true
});
const protoDescriptor = grpc.loadPackageDefinition(packageDefinition);
const productCatalogClient = new protoDescriptor.oteldemo.ProductCatalogService(
  `${process.env.PRODUCT_CATALOG_SERVICE_ADDR}`,
  grpc.credentials.createInsecure()
);

/**
 * Simulate synchronous blocking - this is the anti-pattern
 * Blocks the Node.js event loop for the specified duration
 */
function busyWait(ms) {
  const start = Date.now();
  while (Date.now() - start < ms) {
    // Busy wait - blocks event loop
  }
}

/**
 * Sleep using async setTimeout (normal async behavior)
 */
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Parse and validate search query
 */
async function parseQuery(query, parentSpan) {
  return tracer.startActiveSpan('search.parse_query', async (span) => {
    try {
      span.setAttribute('app.search.query', query);
      span.setAttribute('app.search.query_length', query.length);

      // Simulate parsing work
      await sleep(1);

      const tokens = query.toLowerCase().split(/\s+/).filter(t => t.length > 0);
      span.setAttribute('app.search.tokens_count', tokens.length);

      span.end();
      return tokens;
    } catch (err) {
      span.recordException(err);
      span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
      span.end();
      throw err;
    }
  });
}

/**
 * Check in-memory cache for results
 */
async function checkCache(tokens, parentSpan) {
  return tracer.startActiveSpan('search.check_cache', async (span) => {
    try {
      span.setAttribute('app.search.cache_enabled', true);

      // Simulate cache lookup
      await sleep(2);

      const cacheKey = tokens.join('_');
      const cached = productCache.get(cacheKey);

      span.setAttribute('app.search.cache_hit', !!cached);
      span.setAttribute('app.search.cache_key', cacheKey);

      span.end();
      return cached || null;
    } catch (err) {
      span.recordException(err);
      span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
      span.end();
      throw err;
    }
  });
}

/**
 * Fetch products from ProductCatalogService - NORMAL async mode
 */
async function fetchProductsAsync(tokens, parentSpan) {
  return tracer.startActiveSpan('search.fetch_products', async (span) => {
    try {
      span.setAttribute('app.search.fetch_mode', 'async');

      // Call ProductCatalogService.ListProducts (auto-instrumented)
      const products = await new Promise((resolve, reject) => {
        productCatalogClient.ListProducts({}, (err, response) => {
          if (err) {
            reject(err);
          } else {
            resolve(response.products || []);
          }
        });
      });

      span.setAttribute('app.search.products_fetched', products.length);
      span.end();
      return products;
    } catch (err) {
      span.recordException(err);
      span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
      span.end();
      throw err;
    }
  });
}

/**
 * Fetch products - SYNC-OVER-ASYNC mode (Lollipop anti-pattern)
 * This creates a single dominant child span that blocks the event loop
 */
async function fetchProductsBlocking(tokens, parentSpan) {
  return tracer.startActiveSpan('search.fetch_products_blocking', async (span) => {
    try {
      const blockDuration = 2000 + Math.floor(Math.random() * 1000); // 2000-3000ms
      const fetchStart = Date.now();

      span.setAttribute('app.search.fetch_mode', 'sync_over_async');
      span.setAttribute('app.search.blocking', true);
      span.setAttribute('app.search.block_reason', 'synchronous_fetch');
      span.setAttribute('app.search.block_duration_ms', blockDuration);

      // First, make the actual gRPC call (which will be fast, ~10-20ms, auto-instrumented)
      const products = await new Promise((resolve, reject) => {
        productCatalogClient.ListProducts({}, (err, response) => {
          if (err) {
            reject(err);
          } else {
            resolve(response.products || []);
          }
        });
      });

      const fetchDuration = Date.now() - fetchStart;
      logger.info({ fetchDuration }, 'ProductCatalogService.ListProducts completed');

      // NOW the anti-pattern: block the event loop to simulate sync-over-async
      // This makes the span duration match the blocking time, not the actual work
      const remainingBlock = Math.max(0, blockDuration - fetchDuration);
      if (remainingBlock > 0) {
        logger.warn({ remainingBlock }, 'Blocking event loop (sync-over-async anti-pattern)');
        busyWait(remainingBlock);
      }

      const totalDuration = Date.now() - fetchStart;
      span.setAttribute('app.search.products_fetched', products.length);
      span.setAttribute('app.search.actual_fetch_ms', fetchDuration);
      span.setAttribute('app.search.total_blocked_ms', totalDuration);

      span.end();
      return products;
    } catch (err) {
      span.recordException(err);
      span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
      span.end();
      throw err;
    }
  });
}

/**
 * Rank and filter search results
 */
async function rankResults(products, tokens, maxResults, parentSpan) {
  return tracer.startActiveSpan('search.rank_results', async (span) => {
    try {
      span.setAttribute('app.search.products_to_rank', products.length);

      // Simulate ranking work
      await sleep(3);

      // Simple ranking: filter by tokens, score by matches
      const results = products
        .map(product => {
          const nameLower = product.name.toLowerCase();
          const descLower = (product.description || '').toLowerCase();

          let score = 0;
          tokens.forEach(token => {
            if (nameLower.includes(token)) score += 10;
            if (descLower.includes(token)) score += 5;
          });

          return {
            product_id: product.id,
            name: product.name,
            relevance_score: score,
            price: product.price_usd
          };
        })
        .filter(r => r.relevance_score > 0)
        .sort((a, b) => b.relevance_score - a.relevance_score)
        .slice(0, maxResults || 10);

      span.setAttribute('app.search.results_count', results.length);
      span.setAttribute('app.search.max_results', maxResults || 10);

      span.end();
      return results;
    } catch (err) {
      span.recordException(err);
      span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
      span.end();
      throw err;
    }
  });
}

/**
 * Main search function
 */
module.exports.searchProducts = async (request) => {
  const startTime = Date.now();
  const span = tracer.startSpan('SearchProducts');

  try {
    // Initialize feature flag provider
    await OpenFeature.setProviderAndWait(flagProvider);

    // Check if sync-over-async mode is enabled
    const syncOverAsyncEnabled = await OpenFeature.getClient().getBooleanValue('searchServiceSyncOverAsync', false);

    const mode = syncOverAsyncEnabled ? 'sync_over_async' : 'normal';
    span.setAttribute('app.search.mode', mode);
    span.setAttribute('app.search.query', request.query);

    logger.info({
      query: request.query,
      mode,
      maxResults: request.max_results
    }, 'Search request received');

    // Step 1: Parse query
    const tokens = await parseQuery(request.query, span);

    // Step 2: Check cache
    const cached = await checkCache(tokens, span);

    let products;
    if (cached) {
      products = cached;
      span.setAttribute('app.search.cache_used', true);
    } else {
      // Step 3: Fetch products (mode-dependent)
      if (syncOverAsyncEnabled) {
        // LOLLIPOP MODE: Blocking fetch
        products = await fetchProductsBlocking(tokens, span);
      } else {
        // NORMAL MODE: Async fetch
        products = await fetchProductsAsync(tokens, span);
      }
      span.setAttribute('app.search.cache_used', false);
    }

    // Step 4: Rank results
    const results = await rankResults(products, tokens, request.max_results, span);

    const searchDuration = Date.now() - startTime;
    span.setAttribute('app.search.total_results', results.length);
    span.setAttribute('app.search.duration_ms', searchDuration);

    logger.info({
      query: request.query,
      mode,
      resultsCount: results.length,
      durationMs: searchDuration
    }, 'Search completed');

    span.end();

    return {
      results,
      total_matches: results.length,
      search_duration_ms: searchDuration
    };

  } catch (err) {
    logger.error({ err }, 'Search failed');
    span.recordException(err);
    span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
    span.end();
    throw err;
  }
};

/**
 * Get search suggestions (autocomplete)
 */
module.exports.getSuggestions = async (request) => {
  const span = tracer.startSpan('GetSuggestions');

  try {
    span.setAttribute('app.search.partial_query', request.partial_query);

    // Simple stub - return static suggestions
    const suggestions = [
      'telescope',
      'solar system',
      'constellation map',
      'space poster',
      'astronomy book'
    ].filter(s => s.startsWith(request.partial_query.toLowerCase()))
     .slice(0, request.max_suggestions || 5);

    span.setAttribute('app.search.suggestions_count', suggestions.length);
    span.end();

    return { suggestions };

  } catch (err) {
    logger.error({ err }, 'Get suggestions failed');
    span.recordException(err);
    span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
    span.end();
    throw err;
  }
};
