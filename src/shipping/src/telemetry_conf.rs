// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use tracing_subscriber::EnvFilter;

/// Plain stdout logging; no OpenTelemetry SDK, exporters or log bridge.
/// Telemetry for this service is produced externally (eBPF / OBI).
pub fn init_logging() {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::new("info"))
        .with_ansi(false)
        .init();
}
