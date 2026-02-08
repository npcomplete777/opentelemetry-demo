// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"fmt"
	"log"
	"log/slog"
	"math/rand"
	"net"
	"os"
	"os/signal"
	"sync"
	"time"

	flagd "github.com/open-feature/go-sdk-contrib/providers/flagd/pkg"
	"github.com/open-feature/go-sdk/openfeature"
	otelhooks "github.com/open-feature/go-sdk-contrib/hooks/open-telemetry/pkg"

	pb "github.com/open-telemetry/opentelemetry-demo/src/pricing/genproto/oteldemo"

	"go.opentelemetry.io/contrib/bridges/otelslog"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"go.opentelemetry.io/contrib/instrumentation/runtime"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	otelcodes "go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploggrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/propagation"
	sdklog "go.opentelemetry.io/otel/sdk/log"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
	"golang.org/x/sync/semaphore"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
)

//go:generate go install google.golang.org/protobuf/cmd/protoc-gen-go
//go:generate go install google.golang.org/grpc/cmd/protoc-gen-go-grpc
//go:generate protoc --go_out=./ --go-grpc_out=./ --proto_path=../../pb ../../pb/demo.proto

var (
	logger *slog.Logger
	tracer trace.Tracer
)

// Base product prices (USD)
var basePrices = map[string]*pb.Money{
	"OLJCESPC7Z": {CurrencyCode: "USD", Units: 66, Nanos: 0},   // Vintage Typewriter
	"66VCHSJNUP": {CurrencyCode: "USD", Units: 28, Nanos: 99},  // Vintage Camera Lens
	"1YMWWN1N4O": {CurrencyCode: "USD", Units: 249, Nanos: 99}, // Home Barista Kit
	"L9ECAV7KIM": {CurrencyCode: "USD", Units: 60, Nanos: 0},   // Terrarium
	"2ZYFJ3GM2N": {CurrencyCode: "USD", Units: 30, Nanos: 50},  // Film Camera
	"0PUK6V6EV0": {CurrencyCode: "USD", Units: 95, Nanos: 0},   // Vintage Record Player
	"LS4PSXUNUM": {CurrencyCode: "USD", Units: 65, Nanos: 0},   // Metal Camping Mug
	"9SIQT8TOJO": {CurrencyCode: "USD", Units: 45, Nanos: 0},   // City Bike
	"6E92ZMYYFZ": {CurrencyCode: "USD", Units: 22, Nanos: 50},  // Air Plant
}

type pricingServer struct {
	pb.UnimplementedPricingServiceServer
	productCatalogAddr string
	currencyAddr       string
	productClient      pb.ProductCatalogServiceClient
	currencyClient     pb.CurrencyServiceClient
	sem                *semaphore.Weighted // For bounded concurrency
}

func initOpenTelemetry(ctx context.Context) (*sdktrace.TracerProvider, *sdkmetric.MeterProvider, *sdklog.LoggerProvider, error) {
	res, err := resource.New(ctx,
		resource.WithFromEnv(),
		resource.WithTelemetrySDK(),
		resource.WithHost(),
		resource.WithContainer(),
	)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("failed to create resource: %w", err)
	}

	// Trace provider
	traceExporter, err := otlptracegrpc.New(ctx)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("failed to create trace exporter: %w", err)
	}

	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(traceExporter),
		sdktrace.WithResource(res),
	)
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	// Metric provider
	metricExporter, err := otlpmetricgrpc.New(ctx)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("failed to create metric exporter: %w", err)
	}

	mp := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metricExporter)),
		sdkmetric.WithResource(res),
	)
	otel.SetMeterProvider(mp)

	// Start runtime metrics
	if err := runtime.Start(runtime.WithMinimumReadMemStatsInterval(time.Second)); err != nil {
		return nil, nil, nil, fmt.Errorf("failed to start runtime instrumentation: %w", err)
	}

	// Log provider
	logExporter, err := otlploggrpc.New(ctx)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("failed to create log exporter: %w", err)
	}

	lp := sdklog.NewLoggerProvider(
		sdklog.WithProcessor(sdklog.NewBatchProcessor(logExporter)),
		sdklog.WithResource(res),
	)
	global.SetLoggerProvider(lp)

	return tp, mp, lp, nil
}

func (s *pricingServer) GetProductPrice(ctx context.Context, req *pb.GetProductPriceRequest) (*pb.GetProductPriceResponse, error) {
	span := trace.SpanFromContext(ctx)
	span.SetAttributes(
		attribute.String("app.pricing.product_id", req.ProductId),
		attribute.String("app.pricing.currency", req.CurrencyCode),
		attribute.Int("app.pricing.quantity", int(req.Quantity)),
	)

	logger.Info("GetProductPrice called",
		slog.String("product_id", req.ProductId),
		slog.String("currency", req.CurrencyCode),
	)

	// Get base price
	basePrice, ok := basePrices[req.ProductId]
	if !ok {
		return nil, status.Errorf(codes.NotFound, "product %s not found", req.ProductId)
	}

	// Convert currency if needed
	finalPrice := basePrice
	if req.CurrencyCode != "USD" && s.currencyClient != nil {
		converted, err := s.currencyClient.Convert(ctx, &pb.CurrencyConversionRequest{
			From:   basePrice,
			ToCode: req.CurrencyCode,
		})
		if err != nil {
			logger.Warn("Currency conversion failed, using USD", slog.String("error", err.Error()))
		} else {
			finalPrice = converted
		}
	}

	// Apply quantity discount
	discountPercent := 0.0
	if req.Quantity >= 10 {
		discountPercent = 10.0
	} else if req.Quantity >= 5 {
		discountPercent = 5.0
	}

	discountedPrice := finalPrice
	if discountPercent > 0 {
		multiplier := 1.0 - (discountPercent / 100.0)
		discountedPrice = &pb.Money{
			CurrencyCode: finalPrice.CurrencyCode,
			Units:        int64(float64(finalPrice.Units) * multiplier),
			Nanos:        finalPrice.Nanos,
		}
	}

	return &pb.GetProductPriceResponse{
		Price: &pb.ProductPrice{
			ProductId:       req.ProductId,
			BasePrice:       finalPrice,
			DiscountedPrice: discountedPrice,
			DiscountPercent: discountPercent,
			CurrencyCode:    finalPrice.CurrencyCode,
		},
	}, nil
}

func (s *pricingServer) GetBulkPrices(ctx context.Context, req *pb.GetBulkPricesRequest) (*pb.GetBulkPricesResponse, error) {
	span := trace.SpanFromContext(ctx)
	span.SetAttributes(
		attribute.Int("app.pricing.product_count", len(req.ProductIds)),
		attribute.String("app.pricing.currency", req.CurrencyCode),
	)

	logger.Info("GetBulkPrices called",
		slog.Int("product_count", len(req.ProductIds)),
		slog.String("currency", req.CurrencyCode),
	)

	// Check if unbounded fan-out mode is enabled
	client := openfeature.NewClient("pricing")
	unboundedFanOut, _ := client.BooleanValue(ctx, "pricingServiceUnboundedFanOut", false, openfeature.EvaluationContext{})

	if unboundedFanOut {
		return s.getBulkPricesUnbounded(ctx, req)
	}

	return s.getBulkPricesBounded(ctx, req)
}

// Normal mode: Bounded concurrency using semaphore (max 3 concurrent)
func (s *pricingServer) getBulkPricesBounded(ctx context.Context, req *pb.GetBulkPricesRequest) (*pb.GetBulkPricesResponse, error) {
	span := trace.SpanFromContext(ctx)
	span.SetAttributes(
		attribute.String("app.pricing.mode", "normal"),
		attribute.Int("app.pricing.concurrency_limit", 3),
	)

	prices := make([]*pb.ProductPrice, len(req.ProductIds))
	var wg sync.WaitGroup
	var mu sync.Mutex

	for i, productID := range req.ProductIds {
		wg.Add(1)
		go func(idx int, pid string) {
			defer wg.Done()

			// Acquire semaphore (bounded concurrency)
			if err := s.sem.Acquire(ctx, 1); err != nil {
				logger.Error("Failed to acquire semaphore", slog.String("error", err.Error()))
				return
			}
			defer s.sem.Release(1)

			ctx, calcSpan := tracer.Start(ctx, "price.calculate",
				trace.WithAttributes(
					attribute.String("app.pricing.product_id", pid),
					attribute.Int("app.pricing.worker_index", idx),
					attribute.Bool("app.pricing.bounded", true),
				),
			)
			defer calcSpan.End()

			// Get product details
			if s.productClient != nil {
				_, err := s.productClient.GetProduct(ctx, &pb.GetProductRequest{Id: pid})
				if err != nil {
					logger.Warn("Failed to get product", slog.String("product_id", pid), slog.String("error", err.Error()))
				}
			}

			// Get base price
			basePrice, ok := basePrices[pid]
			if !ok {
				calcSpan.SetStatus(otelcodes.Error, "product not found")
				return
			}

			// Convert currency
			finalPrice := basePrice
			if req.CurrencyCode != "USD" && s.currencyClient != nil {
				converted, err := s.currencyClient.Convert(ctx, &pb.CurrencyConversionRequest{
					From:   basePrice,
					ToCode: req.CurrencyCode,
				})
				if err == nil {
					finalPrice = converted
				}
			}

			// Simulate pricing computation
			time.Sleep(time.Duration(30+rand.Intn(50)) * time.Millisecond)

			mu.Lock()
			prices[idx] = &pb.ProductPrice{
				ProductId:       pid,
				BasePrice:       finalPrice,
				DiscountedPrice: finalPrice,
				DiscountPercent: 0,
				CurrencyCode:    finalPrice.CurrencyCode,
			}
			mu.Unlock()
		}(i, productID)
	}

	wg.Wait()
	return &pb.GetBulkPricesResponse{Prices: prices}, nil
}

// Anti-pattern mode: UNBOUNDED fan-out (Expanding Fan geometry)
func (s *pricingServer) getBulkPricesUnbounded(ctx context.Context, req *pb.GetBulkPricesRequest) (*pb.GetBulkPricesResponse, error) {
	span := trace.SpanFromContext(ctx)
	span.SetAttributes(
		attribute.String("app.pricing.mode", "unbounded_fan_out"),
		attribute.Int("app.pricing.product_count", len(req.ProductIds)),
		attribute.Int("app.pricing.concurrency_limit", 0), // 0 = unbounded
	)

	logger.Warn("ANTI-PATTERN: Unbounded fan-out enabled",
		slog.Int("goroutines", len(req.ProductIds)),
	)

	prices := make([]*pb.ProductPrice, len(req.ProductIds))
	var wg sync.WaitGroup
	var mu sync.Mutex

	// Launch ALL goroutines immediately with NO limit
	for i, productID := range req.ProductIds {
		wg.Add(1)
		go func(idx int, pid string) {
			defer wg.Done()

			// Each goroutine creates its own span - all start concurrently
			ctx, calcSpan := tracer.Start(ctx, "price.calculate",
				trace.WithAttributes(
					attribute.String("app.pricing.product_id", pid),
					attribute.Int("app.pricing.goroutine_index", idx),
					attribute.Bool("app.pricing.unbounded", true),
				),
			)
			defer calcSpan.End()

			// Real downstream calls - this is where the fan-out blasts the services
			if s.productClient != nil {
				_, err := s.productClient.GetProduct(ctx, &pb.GetProductRequest{Id: pid})
				if err != nil {
					logger.Warn("Failed to get product", slog.String("product_id", pid), slog.String("error", err.Error()))
				}
			}

			// Get base price
			basePrice, ok := basePrices[pid]
			if !ok {
				calcSpan.SetStatus(otelcodes.Error, "product not found")
				return
			}

			// Currency conversion - another blast to currency service
			finalPrice := basePrice
			if req.CurrencyCode != "USD" && s.currencyClient != nil {
				converted, err := s.currencyClient.Convert(ctx, &pb.CurrencyConversionRequest{
					From:   basePrice,
					ToCode: req.CurrencyCode,
				})
				if err == nil {
					finalPrice = converted
				}
			}

			// Simulate pricing computation
			// Later goroutines take longer due to resource contention
			baseDuration := 30 + rand.Intn(50)
			contentionPenalty := idx * 5 // Linear degradation
			totalDuration := baseDuration + contentionPenalty
			time.Sleep(time.Duration(totalDuration) * time.Millisecond)

			mu.Lock()
			prices[idx] = &pb.ProductPrice{
				ProductId:       pid,
				BasePrice:       finalPrice,
				DiscountedPrice: finalPrice,
				DiscountPercent: 0,
				CurrencyCode:    finalPrice.CurrencyCode,
			}
			mu.Unlock()
		}(i, productID)
	}

	wg.Wait()
	return &pb.GetBulkPricesResponse{Prices: prices}, nil
}

func (s *pricingServer) CalculateDiscount(ctx context.Context, req *pb.CalculateDiscountRequest) (*pb.CalculateDiscountResponse, error) {
	span := trace.SpanFromContext(ctx)
	span.SetAttributes(
		attribute.Int("app.pricing.items_count", len(req.Items)),
		attribute.String("app.pricing.promo_code", req.PromoCode),
	)

	// Simple stub implementation
	totalUnits := int64(0)
	for _, item := range req.Items {
		if basePrice, ok := basePrices[item.ProductId]; ok {
			totalUnits += basePrice.Units * int64(item.Quantity)
		}
	}

	total := &pb.Money{CurrencyCode: "USD", Units: totalUnits, Nanos: 0}
	discountPercent := 0.0

	// Apply promo code
	if req.PromoCode == "SAVE10" {
		discountPercent = 10.0
	}

	discounted := total
	if discountPercent > 0 {
		multiplier := 1.0 - (discountPercent / 100.0)
		discounted = &pb.Money{
			CurrencyCode: "USD",
			Units:        int64(float64(total.Units) * multiplier),
			Nanos:        total.Nanos,
		}
	}

	return &pb.CalculateDiscountResponse{
		TotalBeforeDiscount: total,
		TotalAfterDiscount:  discounted,
		DiscountPercent:     discountPercent,
	}, nil
}

func main() {
	ctx := context.Background()

	// Initialize OpenTelemetry
	tp, mp, lp, err := initOpenTelemetry(ctx)
	if err != nil {
		log.Fatalf("Failed to initialize OpenTelemetry: %v", err)
	}
	defer func() {
		_ = tp.Shutdown(ctx)
		_ = mp.Shutdown(ctx)
		_ = lp.Shutdown(ctx)
	}()

	// Set up logger
	logger = otelslog.NewLogger("pricing")
	tracer = otel.Tracer("pricing")

	// Initialize OpenFeature with flagd
	flagdProvider, _ := flagd.NewProvider()
	openfeature.SetProvider(flagdProvider)
	openfeature.AddHooks(otelhooks.NewTracesHook())

	// Get service addresses
	productCatalogAddr := os.Getenv("PRODUCT_CATALOG_ADDR")
	if productCatalogAddr == "" {
		productCatalogAddr = "product-catalog:8080"
	}

	currencyAddr := os.Getenv("CURRENCY_ADDR")
	if currencyAddr == "" {
		currencyAddr = "currency:8080"
	}

	port := os.Getenv("PRICING_PORT")
	if port == "" {
		port = "8080"
	}

	// Create gRPC clients
	var productClient pb.ProductCatalogServiceClient
	var currencyClient pb.CurrencyServiceClient

	if conn, err := grpc.NewClient(productCatalogAddr, grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithStatsHandler(otelgrpc.NewClientHandler())); err == nil {
		productClient = pb.NewProductCatalogServiceClient(conn)
	}

	if conn, err := grpc.NewClient(currencyAddr, grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithStatsHandler(otelgrpc.NewClientHandler())); err == nil {
		currencyClient = pb.NewCurrencyServiceClient(conn)
	}

	// Create pricing server with semaphore for bounded concurrency (max 3)
	server := &pricingServer{
		productCatalogAddr: productCatalogAddr,
		currencyAddr:       currencyAddr,
		productClient:      productClient,
		currencyClient:     currencyClient,
		sem:                semaphore.NewWeighted(3),
	}

	// Start gRPC server
	lis, err := net.Listen("tcp", ":"+port)
	if err != nil {
		log.Fatalf("Failed to listen: %v", err)
	}

	grpcServer := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)
	pb.RegisterPricingServiceServer(grpcServer, server)

	logger.Info("Pricing service starting", slog.String("port", port))

	// Graceful shutdown
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, os.Interrupt)
		<-sigCh
		logger.Info("Shutting down pricing service")
		grpcServer.GracefulStop()
	}()

	if err := grpcServer.Serve(lis); err != nil {
		log.Fatalf("Failed to serve: %v", err)
	}
}
