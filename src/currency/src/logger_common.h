// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <ctime>
#include <iostream>
#include <mutex>
#include <string>

// Minimal stdout logger. No OpenTelemetry SDK/exporter: telemetry for this
// service is produced externally (eBPF / OBI).
namespace
{
class StdoutLogger
{
public:
  explicit StdoutLogger(std::string name) : name_(std::move(name)) {}

  void Info(const std::string &msg) { Write("INFO", msg); }
  void Error(const std::string &msg) { Write("ERROR", msg); }

private:
  void Write(const char *level, const std::string &msg)
  {
    char ts[32];
    std::time_t now = std::time(nullptr);
    std::strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&now));
    std::lock_guard<std::mutex> lock(mu_);
    std::cout << ts << " " << level << " " << name_ << ": " << msg << std::endl;
  }

  std::string name_;
  std::mutex mu_;
};
}  // namespace
