// Dash0 Web SDK initialization for Website Monitoring (RUM, sessions, web vitals)
// Sends directly to Dash0 via same-origin reverse proxy (/dash0-ingest/ → ingress.us-west-2.aws.dash0.com)
// The proxy preserves the original OTLP payload with dash0-web-sdk scope, which Dash0 uses
// to categorize data as Website Monitoring (not server-side traces).

import { init, sendEvent, reportError, addSignalAttribute } from '@dash0/sdk-web';

const {
  NEXT_PUBLIC_OTEL_SERVICE_NAME = '',
  NEXT_PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = '',
} = typeof window !== 'undefined' ? window.ENV : {};

const Dash0Init = () => {
  if (typeof window === 'undefined') return;

  // Derive same-origin base URL for the Dash0 ingest proxy
  // e.g. "http://localhost:8080/otlp-http/v1/traces" → "http://localhost:8080"
  const tracesEndpoint = NEXT_PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT;
  if (!tracesEndpoint) {
    console.warn('[Dash0 Web SDK] No OTLP endpoint configured — skipping initialization');
    return;
  }

  // Extract origin (scheme + host + port) from the existing OTLP endpoint
  let origin: string;
  try {
    const url = new URL(tracesEndpoint);
    origin = url.origin;
  } catch {
    // Fallback for relative URLs
    origin = window.location.origin;
  }

  // The SDK sends to {url}/v1/traces and {url}/v1/logs
  // Our proxy route /dash0-ingest/ forwards to Dash0 ingress with auth injected server-side
  const proxyEndpoint = `${origin}/dash0-ingest`;

  init({
    serviceName: NEXT_PUBLIC_OTEL_SERVICE_NAME || 'frontend-web',
    endpoint: {
      url: proxyEndpoint,
      // Auth token for Website Monitoring — with Ingesting permissions only
      // The Nginx proxy also injects this server-side, but the SDK requires it
      authToken: 'auth_uG2giIHVL1Xf0fW8CRx8NLlJJS1s0uA1',
    },
    // Enable all instrumentations for full Website Monitoring coverage
    // Navigation: page views, route transitions
    // Web Vitals: LCP, FID, CLS, INP, TTFB
    // Error: unhandled errors, promise rejections
    // Fetch: HTTP request tracking with timing
    enabledInstrumentations: ['@dash0/navigation', '@dash0/web-vitals', '@dash0/error', '@dash0/fetch'],
  });

  // Add custom attribute to identify this deployment
  addSignalAttribute('deployment.environment.name', 'k3d-demo');

  // Send a custom event to verify data flow
  sendEvent('dash0_sdk_initialized', {
    title: 'Dash0 Web SDK initialized successfully',
    data: 'frontend-web',
    severity: 'INFO',
  });

  console.log('[Dash0 Web SDK] Initialized — sending to', proxyEndpoint);
};

export default Dash0Init;
