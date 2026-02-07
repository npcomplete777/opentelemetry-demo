/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package oteldemo;

import dev.openfeature.contrib.providers.flagd.FlagdOptions;
import dev.openfeature.contrib.providers.flagd.FlagdProvider;
import dev.openfeature.sdk.Client;
import dev.openfeature.sdk.EvaluationContext;
import dev.openfeature.sdk.MutableContext;
import dev.openfeature.sdk.OpenFeatureAPI;
import io.grpc.*;
import io.grpc.health.v1.HealthCheckResponse.ServingStatus;
import io.grpc.protobuf.services.*;
import io.grpc.stub.StreamObserver;
import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Scope;
import java.io.IOException;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicInteger;
import org.apache.logging.log4j.Level;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import oteldemo.Demo.*;

public final class InventoryService {

  private static final Logger logger = LogManager.getLogger(InventoryService.class);
  private static final Tracer tracer = GlobalOpenTelemetry.getTracer("inventory");
  private static final String RETRY_STORM_FEATURE_FLAG = "inventoryServiceRetryStorm";

  private Server server;
  private HealthStatusManager healthMgr;

  // In-memory stock tracking
  private static final Map<String, StockLevel> stockLevels = new ConcurrentHashMap<>();
  private static final Map<String, Reservation> reservations = new ConcurrentHashMap<>();

  // Product catalog IDs from the demo
  private static final List<String> PRODUCT_IDS = Arrays.asList(
      "OLJCESPC7Z", "66VCHSJNUP", "1YMWWN1N4O", "L9ECAV7KIM",
      "2ZYFJ3GM2N", "0PUK6V6EV0", "LS4PSXUNUM", "9SIQT8TOJO",
      "6E92ZMYYFZ");

  static class StockLevel {
    AtomicInteger available;
    AtomicInteger reserved;

    StockLevel(int initialStock) {
      this.available = new AtomicInteger(initialStock);
      this.reserved = new AtomicInteger(0);
    }
  }

  static class Reservation {
    String orderId;
    List<StockItem> items;
    long timestamp;

    Reservation(String orderId, List<StockItem> items) {
      this.orderId = orderId;
      this.items = items;
      this.timestamp = System.currentTimeMillis();
    }
  }

  private void start() throws IOException {
    int port =
        Integer.parseInt(
            Optional.ofNullable(System.getenv("INVENTORY_PORT"))
                .orElseThrow(
                    () ->
                        new IllegalStateException(
                            "environment vars: INVENTORY_PORT must not be null")));
    healthMgr = new HealthStatusManager();

    // Initialize stock levels
    initializeStock();

    // Create a flagd instance with OpenTelemetry
    FlagdOptions options =
        FlagdOptions.builder()
            .withGlobalTelemetry(true)
            .build();

    FlagdProvider flagdProvider = new FlagdProvider(options);
    OpenFeatureAPI.getInstance().setProvider(flagdProvider);

    server =
        ServerBuilder.forPort(port)
            .addService(new InventoryServiceImpl())
            .addService(healthMgr.getHealthService())
            .build()
            .start();
    logger.info("Inventory service started, listening on " + port);
    Runtime.getRuntime()
        .addShutdownHook(
            new Thread(
                () -> {
                  System.err.println(
                      "*** shutting down gRPC inventory server since JVM is shutting down");
                  InventoryService.this.stop();
                  System.err.println("*** server shut down");
                }));
    healthMgr.setStatus("", ServingStatus.SERVING);
  }

  private void stop() {
    if (server != null) {
      healthMgr.clearStatus("");
      server.shutdown();
    }
  }

  private static void initializeStock() {
    // Initialize each product with random stock between 50-200 units
    for (String productId : PRODUCT_IDS) {
      int initialStock = 50 + ThreadLocalRandom.current().nextInt(151);
      stockLevels.put(productId, new StockLevel(initialStock));
      logger.info("Initialized stock for {}: {} units", productId, initialStock);
    }
  }

  private static class InventoryServiceImpl extends oteldemo.InventoryServiceGrpc.InventoryServiceImplBase {

    private static final Client ffClient = OpenFeatureAPI.getInstance().getClient();

    private InventoryServiceImpl() {}

    @Override
    public void reserveStock(ReserveStockRequest req, StreamObserver<ReserveStockResponse> responseObserver) {
      Span parentSpan = Span.current();
      MutableContext evaluationContext = new MutableContext();

      try {
        boolean retryStormEnabled = ffClient.getBooleanValue(RETRY_STORM_FEATURE_FLAG, false, evaluationContext);

        if (retryStormEnabled) {
          parentSpan.setAttribute("app.retry.mode", "retry_storm");
          reserveStockWithRetryStorm(req, responseObserver, parentSpan);
        } else {
          parentSpan.setAttribute("app.retry.mode", "normal");
          reserveStockNormal(req, responseObserver, parentSpan);
        }
      } catch (Exception e) {
        logger.log(Level.ERROR, "ReserveStock failed with exception", e);
        parentSpan.setStatus(StatusCode.ERROR);
        parentSpan.addEvent(
            "Error",
            Attributes.of(AttributeKey.stringKey("exception.message"), e.getMessage()));
        responseObserver.onError(
            Status.INTERNAL.withDescription(e.getMessage()).asRuntimeException());
      }
    }

    private void reserveStockNormal(
        ReserveStockRequest req,
        StreamObserver<ReserveStockResponse> responseObserver,
        Span parentSpan) {

      ReserveStockResponse.Builder responseBuilder = ReserveStockResponse.newBuilder();
      List<StockItemStatus> itemStatuses = new ArrayList<>();
      boolean allAvailable = true;

      // Check all items
      for (StockItem item : req.getItemsList()) {
        StockLevel stockLevel = stockLevels.get(item.getProductId());
        if (stockLevel == null) {
          // Product doesn't exist
          itemStatuses.add(
              StockItemStatus.newBuilder()
                  .setProductId(item.getProductId())
                  .setAvailable(false)
                  .setAvailableQuantity(0)
                  .setRequestedQuantity(item.getQuantity())
                  .build());
          allAvailable = false;
        } else {
          int currentAvailable = stockLevel.available.get();
          boolean available = currentAvailable >= item.getQuantity();
          itemStatuses.add(
              StockItemStatus.newBuilder()
                  .setProductId(item.getProductId())
                  .setAvailable(available)
                  .setAvailableQuantity(currentAvailable)
                  .setRequestedQuantity(item.getQuantity())
                  .build());
          if (!available) {
            allAvailable = false;
          }
        }
      }

      if (allAvailable) {
        // Reserve stock
        String reservationId = UUID.randomUUID().toString();
        for (StockItem item : req.getItemsList()) {
          StockLevel stockLevel = stockLevels.get(item.getProductId());
          stockLevel.available.addAndGet(-item.getQuantity());
          stockLevel.reserved.addAndGet(item.getQuantity());
        }

        // Store reservation
        reservations.put(reservationId, new Reservation(req.getOrderId(), req.getItemsList()));

        responseBuilder.setSuccess(true).setReservationId(reservationId);
        logger.info("Reserved stock for order {}: {}", req.getOrderId(), reservationId);
      } else {
        responseBuilder.setSuccess(false);
        logger.warn("Insufficient stock for order {}", req.getOrderId());
      }

      responseBuilder.addAllItemStatuses(itemStatuses);
      responseObserver.onNext(responseBuilder.build());
      responseObserver.onCompleted();
    }

    private void reserveStockWithRetryStorm(
        ReserveStockRequest req,
        StreamObserver<ReserveStockResponse> responseObserver,
        Span parentSpan) {

      final int maxRetries = 5;
      final int baseDelayMs = 150;
      final int maxDelayMs = 3000;

      int totalAttempts = 0;

      for (int attempt = 0; attempt < maxRetries; attempt++) {
        totalAttempts++;
        boolean isFinalAttempt = (attempt == maxRetries - 1);

        // Calculate delay with exponential backoff and jitter
        int delay = Math.min(baseDelayMs * (1 << attempt), maxDelayMs);
        int jitter = ThreadLocalRandom.current().nextInt(0, delay / 4 + 1);
        int actualDelay = delay + jitter;

        // Create manual span for this attempt
        Span attemptSpan = tracer.spanBuilder("inventory.reserve.attempt").startSpan();

        try (Scope scope = attemptSpan.makeCurrent()) {
          attemptSpan.setAttribute("app.retry.attempt", attempt);
          attemptSpan.setAttribute("app.retry.delay_ms", actualDelay);
          attemptSpan.setAttribute("app.retry.simulated_error", !isFinalAttempt);

          // Perform actual stock check logic (so span has real duration)
          boolean allAvailable = true;
          for (StockItem item : req.getItemsList()) {
            StockLevel stockLevel = stockLevels.get(item.getProductId());
            if (stockLevel == null || stockLevel.available.get() < item.getQuantity()) {
              allAvailable = false;
              break;
            }
          }

          if (isFinalAttempt) {
            // Final attempt succeeds
            attemptSpan.setAttribute("app.retry.outcome", "success");
            logger.info("Retry attempt {} succeeded for order {}", attempt, req.getOrderId());
          } else {
            // Simulated failure
            attemptSpan.setAttribute("app.retry.outcome", "simulated_failure");
            logger.warn(
                "Retry attempt {} failed (simulated) for order {}, will retry after {}ms",
                attempt,
                req.getOrderId(),
                actualDelay);
          }

        } finally {
          attemptSpan.end();
        }

        // Sleep for backoff delay BETWEEN attempts (not after final attempt)
        if (!isFinalAttempt) {
          try {
            Thread.sleep(actualDelay);
          } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            logger.error("Retry backoff interrupted", e);
            break;
          }
        }

        // If this was the final attempt, actually reserve the stock
        if (isFinalAttempt) {
          parentSpan.setAttribute("app.retry.total_attempts", totalAttempts);
          parentSpan.setAttribute("app.retry.base_delay_ms", baseDelayMs);
          parentSpan.setAttribute("app.retry.outcome", "recovered");
          reserveStockNormal(req, responseObserver, parentSpan);
          return;
        }
      }

      // Should never reach here, but just in case
      parentSpan.setAttribute("app.retry.total_attempts", totalAttempts);
      parentSpan.setAttribute("app.retry.outcome", "max_retries_exceeded");
      responseObserver.onError(
          Status.RESOURCE_EXHAUSTED
              .withDescription("Max retries exceeded")
              .asRuntimeException());
    }

    @Override
    public void releaseStock(ReleaseStockRequest req, StreamObserver<ReleaseStockResponse> responseObserver) {
      Span span = Span.current();
      try {
        Reservation reservation = reservations.remove(req.getReservationId());
        if (reservation == null) {
          logger.warn("Reservation not found: {}", req.getReservationId());
          responseObserver.onNext(ReleaseStockResponse.newBuilder().setSuccess(false).build());
          responseObserver.onCompleted();
          return;
        }

        // Restore stock
        for (StockItem item : reservation.items) {
          StockLevel stockLevel = stockLevels.get(item.getProductId());
          if (stockLevel != null) {
            stockLevel.available.addAndGet(item.getQuantity());
            stockLevel.reserved.addAndGet(-item.getQuantity());
          }
        }

        logger.info("Released stock for reservation {}", req.getReservationId());
        responseObserver.onNext(ReleaseStockResponse.newBuilder().setSuccess(true).build());
        responseObserver.onCompleted();
      } catch (Exception e) {
        logger.log(Level.ERROR, "ReleaseStock failed", e);
        span.setStatus(StatusCode.ERROR);
        responseObserver.onError(Status.INTERNAL.withDescription(e.getMessage()).asRuntimeException());
      }
    }

    @Override
    public void getStockLevel(GetStockLevelRequest req, StreamObserver<GetStockLevelResponse> responseObserver) {
      Span span = Span.current();
      try {
        StockLevel stockLevel = stockLevels.get(req.getProductId());
        if (stockLevel == null) {
          responseObserver.onNext(
              GetStockLevelResponse.newBuilder()
                  .setProductId(req.getProductId())
                  .setAvailable(0)
                  .setReserved(0)
                  .build());
        } else {
          responseObserver.onNext(
              GetStockLevelResponse.newBuilder()
                  .setProductId(req.getProductId())
                  .setAvailable(stockLevel.available.get())
                  .setReserved(stockLevel.reserved.get())
                  .build());
        }
        responseObserver.onCompleted();
      } catch (Exception e) {
        logger.log(Level.ERROR, "GetStockLevel failed", e);
        span.setStatus(StatusCode.ERROR);
        responseObserver.onError(Status.INTERNAL.withDescription(e.getMessage()).asRuntimeException());
      }
    }

    @Override
    public void batchGetStockLevels(
        BatchGetStockLevelRequest req,
        StreamObserver<BatchGetStockLevelResponse> responseObserver) {
      Span span = Span.current();
      try {
        List<GetStockLevelResponse> levels = new ArrayList<>();
        for (String productId : req.getProductIdsList()) {
          StockLevel stockLevel = stockLevels.get(productId);
          if (stockLevel == null) {
            levels.add(
                GetStockLevelResponse.newBuilder()
                    .setProductId(productId)
                    .setAvailable(0)
                    .setReserved(0)
                    .build());
          } else {
            levels.add(
                GetStockLevelResponse.newBuilder()
                    .setProductId(productId)
                    .setAvailable(stockLevel.available.get())
                    .setReserved(stockLevel.reserved.get())
                    .build());
          }
        }
        responseObserver.onNext(BatchGetStockLevelResponse.newBuilder().addAllLevels(levels).build());
        responseObserver.onCompleted();
      } catch (Exception e) {
        logger.log(Level.ERROR, "BatchGetStockLevels failed", e);
        span.setStatus(StatusCode.ERROR);
        responseObserver.onError(Status.INTERNAL.withDescription(e.getMessage()).asRuntimeException());
      }
    }
  }

  private void blockUntilShutdown() throws InterruptedException {
    if (server != null) {
      server.awaitTermination();
    }
  }

  public static void main(String[] args) throws IOException, InterruptedException {
    logger.info("Inventory service starting.");
    final InventoryService service = new InventoryService();
    service.start();
    service.blockUntilShutdown();
  }
}
