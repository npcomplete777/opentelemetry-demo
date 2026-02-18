// Dash0 Web SDK initialization for website monitoring (RUM, sessions, web vitals)
// Runs alongside the existing OTel browser tracer — sends directly to Dash0's OTLP endpoint

import { init } from '@dash0/sdk-web';

const {
  DASH0_WEB_ENDPOINT_URL = '',
  DASH0_WEB_AUTH_TOKEN = '',
  NEXT_PUBLIC_OTEL_SERVICE_NAME = '',
} = typeof window !== 'undefined' ? window.ENV : {};

const Dash0Init = () => {
  if (!DASH0_WEB_ENDPOINT_URL || !DASH0_WEB_AUTH_TOKEN) {
    console.warn('[Dash0 Web SDK] Missing endpoint URL or auth token — skipping initialization');
    return;
  }

  init({
    serviceName: NEXT_PUBLIC_OTEL_SERVICE_NAME || 'frontend-web',
    endpoint: {
      url: DASH0_WEB_ENDPOINT_URL,
      authToken: DASH0_WEB_AUTH_TOKEN,
    },
  });
};

export default Dash0Init;
