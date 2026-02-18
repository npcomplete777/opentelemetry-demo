// Dash0 Web SDK initialization for website monitoring (RUM, sessions, web vitals)
// Routes through the frontend-proxy → OTel collector → Dash0 (avoids browser CORS issues)
// Only enables instrumentations NOT covered by the existing OTel FrontendTracer

import { init } from '@dash0/sdk-web';

const {
  NEXT_PUBLIC_OTEL_SERVICE_NAME = '',
  NEXT_PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = '',
} = typeof window !== 'undefined' ? window.ENV : {};

const Dash0Init = () => {
  const tracesEndpoint = NEXT_PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT;
  if (!tracesEndpoint) {
    console.warn('[Dash0 Web SDK] No OTLP endpoint configured — skipping initialization');
    return;
  }

  // Strip /v1/traces to get base OTLP endpoint
  // e.g. "http://localhost:8080/otlp-http/v1/traces" → "http://localhost:8080/otlp-http"
  const baseEndpoint = tracesEndpoint.replace(/\/v1\/traces$/, '');

  init({
    serviceName: NEXT_PUBLIC_OTEL_SERVICE_NAME || 'frontend-web',
    endpoint: {
      url: baseEndpoint,
      // Placeholder — collector handles Dash0 auth server-side
      authToken: 'web-sdk',
    },
    // Only enable Dash0-specific instrumentations; fetch is already covered by OTel FrontendTracer
    enabledInstrumentations: ['@dash0/navigation', '@dash0/web-vitals', '@dash0/error'],
  });
};

export default Dash0Init;
