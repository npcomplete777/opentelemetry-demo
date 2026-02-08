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
	"time"

	"github.com/google/uuid"
	flagd "github.com/open-feature/go-sdk-contrib/providers/flagd/pkg"
	"github.com/open-feature/go-sdk/openfeature"
	otelhooks "github.com/open-feature/go-sdk-contrib/hooks/open-telemetry/pkg"

	pb "github.com/open-telemetry/opentelemetry-demo/src/order/genproto/oteldemo"

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

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

//go:generate go install google.golang.org/protobuf/cmd/protoc-gen-go
//go:generate go install google.golang.org/grpc/cmd/protoc-gen-go-grpc
//go:generate protoc --go_out=./ --go-grpc_out=./ --proto_path=../../pb ../../pb/demo.proto

var (
	logger *slog.Logger
	tracer trace.Tracer
)

type orderServer struct {
	pb.UnimplementedOrderServiceServer
	paymentServiceAddr  string
	shippingServiceAddr string
	emailServiceAddr    string
	cartServiceAddr     string
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
		return nil, nil, nil, fmt.Errorf("failed to start runtime metrics: %w", err)
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

func (s *orderServer) CreateOrder(ctx context.Context, req *pb.CreateOrderRequest) (*pb.CreateOrderResponse, error) {
	span := trace.SpanFromContext(ctx)
	span.SetAttributes(
		attribute.String("app.user.id", req.UserId),
		attribute.String("app.cart.id", req.CartId),
		attribute.String("app.currency", req.CurrencyCode),
	)

	logger.Info("CreateOrder called",
		slog.String("user_id", req.UserId),
		slog.String("cart_id", req.CartId),
	)

	// Check if cascading failure mode is enabled
	client := openfeature.NewClient("order")
	cascadingFailure, err := client.BooleanValue(
		ctx, "orderServiceCascadingFailure", false, openfeature.EvaluationContext{},
	)
	if err != nil {
		logger.Warn("Failed to get feature flag", slog.String("error", err.Error()))
		cascadingFailure = false
	}

	mode := "normal"
	if cascadingFailure {
		mode = "cascading_failure"
	}
	span.SetAttributes(attribute.String("app.order.mode", mode))

	orderId := uuid.New().String()

	if cascadingFailure {
		return s.createOrderWithCascadingFailure(ctx, req, orderId)
	}

	return s.createOrderNormal(ctx, req, orderId)
}

func (s *orderServer) createOrderNormal(ctx context.Context, req *pb.CreateOrderRequest, orderId string) (*pb.CreateOrderResponse, error) {
	// Step 1: Validate cart
	if err := s.validateCart(ctx, req.CartId, 1, false); err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "cart validation failed: %v", err)
	}

	// Step 2: Reserve inventory (simulated)
	if err := s.reserveInventory(ctx, req.CartId, 2, false); err != nil {
		return nil, status.Errorf(codes.ResourceExhausted, "inventory reservation failed: %v", err)
	}

	// Step 3: Process payment
	if err := s.processPayment(ctx, req.CreditCard, 3, false); err != nil {
		s.rollbackInventory(ctx, orderId, 6, false)
		return nil, status.Errorf(codes.FailedPrecondition, "payment failed: %v", err)
	}

	// Step 4: Arrange shipping
	if err := s.arrangeShipping(ctx, req.ShippingAddress, 4, false); err != nil {
		s.rollbackInventory(ctx, orderId, 6, false)
		return nil, status.Errorf(codes.Unavailable, "shipping arrangement failed: %v", err)
	}

	// Step 5: Send confirmation
	if err := s.sendConfirmation(ctx, req.UserId, orderId, 5, false); err != nil {
		logger.Warn("Failed to send confirmation email (non-fatal)",
			slog.String("error", err.Error()))
	}

	return &pb.CreateOrderResponse{
		OrderId: orderId,
		Status:  "completed",
	}, nil
}

func (s *orderServer) createOrderWithCascadingFailure(ctx context.Context, req *pb.CreateOrderRequest, orderId string) (*pb.CreateOrderResponse, error) {
	span := trace.SpanFromContext(ctx)
	var wastedStages int
	var wastedMs int64
	failureStage := "payment"

	// Step 1: Validate cart (succeeds)
	if err := s.validateCart(ctx, req.CartId, 1, false); err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "cart validation failed: %v", err)
	}

	// Step 2: Reserve inventory (succeeds)
	if err := s.reserveInventory(ctx, req.CartId, 2, false); err != nil {
		return nil, status.Errorf(codes.ResourceExhausted, "inventory reservation failed: %v", err)
	}

	// Step 3: Process payment (FAILS - this is the inflection point)
	if err := s.processPayment(ctx, req.CreditCard, 3, true); err != nil {
		// Payment failed, but we DON'T stop here (the anti-pattern)
		logger.Warn("Payment failed, but continuing orchestration (anti-pattern)",
			slog.String("error", err.Error()))
	}

	// Step 4: Arrange shipping (WASTED - will fail because payment failed)
	shippingStart := time.Now()
	if err := s.arrangeShipping(ctx, req.ShippingAddress, 4, true); err != nil {
		wastedMs += time.Since(shippingStart).Milliseconds()
		wastedStages++
		logger.Warn("Shipping failed as expected (cascading from payment failure)")
	}

	// Step 5: Send confirmation (WASTED - will fail because order is corrupted)
	emailStart := time.Now()
	if err := s.sendConfirmation(ctx, req.UserId, orderId, 5, true); err != nil {
		wastedMs += time.Since(emailStart).Milliseconds()
		wastedStages++
		logger.Warn("Email failed as expected (cascading from payment failure)")
	}

	// Step 6: Rollback (cleanup attempt)
	s.rollbackInventory(ctx, orderId, 6, false)

	span.SetAttributes(
		attribute.String("app.order.failure_stage", failureStage),
		attribute.Int("app.order.wasted_stages", wastedStages),
		attribute.Int64("app.order.total_wasted_ms", wastedMs),
	)

	return &pb.CreateOrderResponse{
		OrderId:      orderId,
		Status:       "failed",
		FailureStage: failureStage,
		ErrorMessage: "Order failed at payment stage, cascaded to subsequent stages",
	}, nil
}

func (s *orderServer) validateCart(ctx context.Context, cartId string, stepNum int, shouldFail bool) error {
	ctx, span := tracer.Start(ctx, "ValidateCart")
	defer span.End()

	span.SetAttributes(
		attribute.String("app.order.step", "validate_cart"),
		attribute.Int("app.order.step_number", stepNum),
	)

	// Simulate cart service call
	time.Sleep(time.Duration(10+rand.Intn(10)) * time.Millisecond)

	if shouldFail {
		span.SetAttributes(attribute.String("app.order.step_status", "failed"))
		span.SetStatus(otelcodes.Error, "cart validation failed")
		return fmt.Errorf("cart not found or invalid")
	}

	span.SetAttributes(attribute.String("app.order.step_status", "success"))
	return nil
}

func (s *orderServer) reserveInventory(ctx context.Context, cartId string, stepNum int, shouldFail bool) error {
	ctx, span := tracer.Start(ctx, "ReserveInventory")
	defer span.End()

	span.SetAttributes(
		attribute.String("app.order.step", "reserve_inventory"),
		attribute.Int("app.order.step_number", stepNum),
	)

	// Simulate inventory service call
	time.Sleep(time.Duration(15+rand.Intn(10)) * time.Millisecond)

	if shouldFail {
		span.SetAttributes(attribute.String("app.order.step_status", "failed"))
		span.SetStatus(otelcodes.Error, "inventory reservation failed")
		return fmt.Errorf("insufficient inventory")
	}

	span.SetAttributes(attribute.String("app.order.step_status", "success"))
	return nil
}

func (s *orderServer) processPayment(ctx context.Context, card *pb.CreditCardInfo, stepNum int, shouldFail bool) error {
	ctx, span := tracer.Start(ctx, "ProcessPayment")
	defer span.End()

	span.SetAttributes(
		attribute.String("app.order.step", "process_payment"),
		attribute.Int("app.order.step_number", stepNum),
	)

	// Simulate payment service call with realistic delay
	time.Sleep(time.Duration(400+rand.Intn(200)) * time.Millisecond)

	if shouldFail {
		span.SetAttributes(
			attribute.String("app.order.step_status", "failed"),
			attribute.Bool("app.order.cascade_failure", true),
		)
		span.SetStatus(otelcodes.Error, "payment declined")
		return fmt.Errorf("payment declined: insufficient funds")
	}

	span.SetAttributes(attribute.String("app.order.step_status", "success"))
	return nil
}

func (s *orderServer) arrangeShipping(ctx context.Context, addr *pb.Address, stepNum int, wastedCall bool) error {
	ctx, span := tracer.Start(ctx, "ArrangeShipping")
	defer span.End()

	span.SetAttributes(
		attribute.String("app.order.step", "arrange_shipping"),
		attribute.Int("app.order.step_number", stepNum),
	)

	if wastedCall {
		span.SetAttributes(attribute.Bool("app.order.wasted_call", true))
	}

	// Simulate shipping service call with realistic delay
	time.Sleep(time.Duration(700+rand.Intn(300)) * time.Millisecond)

	if wastedCall {
		span.SetAttributes(
			attribute.String("app.order.step_status", "failed"),
			attribute.Bool("app.order.cascade_failure", true),
		)
		span.SetStatus(otelcodes.Error, "shipping failed due to invalid order state")
		return fmt.Errorf("cannot arrange shipping: order payment not confirmed")
	}

	span.SetAttributes(attribute.String("app.order.step_status", "success"))
	return nil
}

func (s *orderServer) sendConfirmation(ctx context.Context, userId, orderId string, stepNum int, wastedCall bool) error {
	ctx, span := tracer.Start(ctx, "SendConfirmation")
	defer span.End()

	span.SetAttributes(
		attribute.String("app.order.step", "send_confirmation"),
		attribute.Int("app.order.step_number", stepNum),
	)

	if wastedCall {
		span.SetAttributes(attribute.Bool("app.order.wasted_call", true))
	}

	// Simulate email service call with realistic delay
	time.Sleep(time.Duration(800+rand.Intn(300)) * time.Millisecond)

	if wastedCall {
		span.SetAttributes(
			attribute.String("app.order.step_status", "failed"),
			attribute.Bool("app.order.cascade_failure", true),
		)
		span.SetStatus(otelcodes.Error, "email failed due to invalid order state")
		return fmt.Errorf("cannot send confirmation: order not completed")
	}

	span.SetAttributes(attribute.String("app.order.step_status", "success"))
	return nil
}

func (s *orderServer) rollbackInventory(ctx context.Context, orderId string, stepNum int, wastedCall bool) error {
	ctx, span := tracer.Start(ctx, "RollbackInventory")
	defer span.End()

	span.SetAttributes(
		attribute.String("app.order.step", "rollback_inventory"),
		attribute.Int("app.order.step_number", stepNum),
	)

	// Simulate inventory release call
	time.Sleep(time.Duration(10+rand.Intn(10)) * time.Millisecond)

	span.SetAttributes(attribute.String("app.order.step_status", "success"))
	return nil
}

func (s *orderServer) GetOrder(ctx context.Context, req *pb.GetOrderRequest) (*pb.GetOrderResponse, error) {
	// Stub implementation
	return &pb.GetOrderResponse{
		Order: &pb.OrderStatus{
			OrderId:   req.OrderId,
			Status:    "completed",
			Stage:     "delivered",
			CreatedAt: time.Now().Unix(),
		},
	}, nil
}

func (s *orderServer) CancelOrder(ctx context.Context, req *pb.CancelOrderRequest) (*pb.CancelOrderResponse, error) {
	// Stub implementation
	return &pb.CancelOrderResponse{
		Success: true,
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

	// Initialize logger
	slogLogger := slog.New(otelslog.NewHandler("order"))
	logger = slogLogger

	// Initialize tracer
	tracer = otel.Tracer("order")

	// Initialize OpenFeature
	provider, err := flagd.NewProvider()
	if err != nil {
		logger.Error(fmt.Sprintf("Error creating flagd provider: %v", err))
	}
	openfeature.SetProvider(provider)
	openfeature.AddHooks(otelhooks.NewTracesHook())

	// Get port from environment
	port := os.Getenv("ORDER_PORT")
	if port == "" {
		port = "8080"
	}

	// Create gRPC server
	lis, err := net.Listen("tcp", fmt.Sprintf(":%s", port))
	if err != nil {
		log.Fatalf("Failed to listen: %v", err)
	}

	grpcServer := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)

	// Get service addresses from environment
	paymentAddr := os.Getenv("PAYMENT_SERVICE_ADDR")
	if paymentAddr == "" {
		paymentAddr = "payment:50051"
	}
	shippingAddr := os.Getenv("SHIPPING_SERVICE_ADDR")
	if shippingAddr == "" {
		shippingAddr = "shipping:50051"
	}
	emailAddr := os.Getenv("EMAIL_SERVICE_ADDR")
	if emailAddr == "" {
		emailAddr = "email:8080"
	}
	cartAddr := os.Getenv("CART_SERVICE_ADDR")
	if cartAddr == "" {
		cartAddr = "cart:7070"
	}

	pb.RegisterOrderServiceServer(grpcServer, &orderServer{
		paymentServiceAddr:  paymentAddr,
		shippingServiceAddr: shippingAddr,
		emailServiceAddr:    emailAddr,
		cartServiceAddr:     cartAddr,
	})

	logger.Info("Order service starting", slog.String("port", port))

	// Handle graceful shutdown
	go func() {
		sigint := make(chan os.Signal, 1)
		signal.Notify(sigint, os.Interrupt)
		<-sigint
		logger.Info("Shutting down server")
		grpcServer.GracefulStop()
	}()

	if err := grpcServer.Serve(lis); err != nil {
		log.Fatalf("Failed to serve: %v", err)
	}
}
