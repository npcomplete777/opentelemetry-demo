#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


# Python
import os
import random
import time
import uuid
import threading
from concurrent import futures

# Pip
import grpc
from opentelemetry import trace, metrics, context
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode

from openfeature import api
from openfeature.contrib.provider.flagd import FlagdProvider
from openfeature.contrib.hook.opentelemetry import TracingHook

# Local
import logging
import demo_pb2
import demo_pb2_grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

# Channel dispatch delays (in seconds)
CHANNEL_DELAYS = {
    "EMAIL": (0.2, 0.5),      # 200-500ms
    "SMS": (0.1, 0.3),        # 100-300ms
    "WEBHOOK": (0.3, 0.8),    # 300-800ms
    "PUSH": (0.15, 0.4)       # 150-400ms
}

# In-memory notification status tracking
notification_statuses = {}


class NotificationService(demo_pb2_grpc.NotificationServiceServicer):
    def SendNotification(self, request, context):
        """Send a notification across configured channels"""
        span = trace.get_current_span()
        span.set_attribute("app.notification.user_id", request.user_id)
        span.set_attribute("app.notification.order_id", request.order_id)
        span.set_attribute("app.notification.type", demo_pb2.NotificationType.Name(request.type))
        span.set_attribute("app.notification.channels_dispatched", len(request.channels))

        notification_id = str(uuid.uuid4())

        # Initialize status tracking
        notification_statuses[notification_id] = {
            "channels": {}
        }

        # Validation (always synchronous)
        time.sleep(0.001)  # Simulate validation (~1ms)

        # Check feature flag for fire-and-forget mode
        if check_feature_flag("notificationServiceFireAndForget"):
            span.set_attribute("app.notification.mode", "fire_and_forget")
            span.set_attribute("app.notification.awaited", False)
            logger.info(f"SendNotification: fire-and-forget mode enabled for {len(request.channels)} channels")
            return self._send_fire_and_forget(request, notification_id, span)
        else:
            span.set_attribute("app.notification.mode", "normal")
            span.set_attribute("app.notification.awaited", True)
            logger.info(f"SendNotification: normal mode (synchronous) for {len(request.channels)} channels")
            return self._send_normal(request, notification_id)

    def _send_normal(self, request, notification_id):
        """Normal mode - wait for all dispatches to complete"""
        for channel in request.channels:
            channel_name = demo_pb2.NotificationChannel.Name(channel)
            self._dispatch_sync(notification_id, channel_name, request.subject, request.body)

        response = demo_pb2.SendNotificationResponse()
        response.notification_id = notification_id
        response.accepted = True
        return response

    def _send_fire_and_forget(self, request, notification_id, parent_span):
        """Fire-and-forget mode - launch threads but don't wait for completion"""
        # Get the current context to propagate to threads
        current_ctx = context.get_current()

        for channel in request.channels:
            channel_name = demo_pb2.NotificationChannel.Name(channel)

            # Initialize channel status as pending
            notification_statuses[notification_id]["channels"][channel_name] = {
                "status": "pending",
                "error_message": ""
            }

            # Launch thread with context propagation
            thread = threading.Thread(
                target=self._dispatch_async,
                args=(current_ctx, notification_id, channel_name, request.subject, request.body)
            )
            thread.daemon = True  # Mark as daemon so it doesn't block shutdown
            thread.start()
            # CRITICAL: Do NOT call thread.join() — this is the fire-and-forget

            logger.debug(f"Dispatched {channel_name} notification in background thread (fire-and-forget)")

        # Return immediately without waiting
        response = demo_pb2.SendNotificationResponse()
        response.notification_id = notification_id
        response.accepted = True
        return response

    def _dispatch_sync(self, notification_id, channel_name, subject, body):
        """Synchronous dispatch with proper span tracking"""
        with tracer.start_as_current_span(f"dispatch_{channel_name.lower()}") as span:
            span.set_attribute("app.notification.channel", channel_name)
            span.set_attribute("app.notification.subject", subject)

            # Simulate dispatch work
            delay_min, delay_max = CHANNEL_DELAYS.get(channel_name, (0.2, 0.5))
            delay = random.uniform(delay_min, delay_max)
            time.sleep(delay)

            # Simulate random failures (20% chance)
            if random.random() < 0.2:
                error_msg = f"Simulated {channel_name} dispatch failure"
                span.set_status(Status(StatusCode.ERROR, error_msg))
                span.set_attribute("app.notification.delivery", "failed")
                notification_statuses[notification_id]["channels"][channel_name] = {
                    "status": "failed",
                    "error_message": error_msg
                }
                logger.error(f"Notification {notification_id} {channel_name} dispatch failed: {error_msg}")
            else:
                span.set_attribute("app.notification.delivery", "delivered")
                notification_statuses[notification_id]["channels"][channel_name] = {
                    "status": "delivered",
                    "error_message": ""
                }
                logger.info(f"Notification {notification_id} {channel_name} dispatched successfully")

    def _dispatch_async(self, ctx, notification_id, channel_name, subject, body):
        """Asynchronous dispatch in a separate thread with context propagation"""
        # Attach the parent context so spans are properly linked
        context.attach(ctx)

        # Create a child span in this thread context
        with tracer.start_as_current_span(f"dispatch_{channel_name.lower()}") as span:
            span.set_attribute("app.notification.channel", channel_name)
            span.set_attribute("app.notification.subject", subject)
            span.set_attribute("app.notification.async", True)

            # Simulate dispatch work
            delay_min, delay_max = CHANNEL_DELAYS.get(channel_name, (0.2, 0.5))
            delay = random.uniform(delay_min, delay_max)
            time.sleep(delay)

            # Simulate random failures (20% chance)
            if random.random() < 0.2:
                error_msg = f"Simulated {channel_name} dispatch failure (async)"
                span.set_status(Status(StatusCode.ERROR, error_msg))
                span.set_attribute("app.notification.delivery", "failed")
                notification_statuses[notification_id]["channels"][channel_name] = {
                    "status": "failed",
                    "error_message": error_msg
                }
                logger.error(f"[ASYNC] Notification {notification_id} {channel_name} dispatch failed: {error_msg}")
            else:
                span.set_attribute("app.notification.delivery", "delivered")
                notification_statuses[notification_id]["channels"][channel_name] = {
                    "status": "delivered",
                    "error_message": ""
                }
                logger.info(f"[ASYNC] Notification {notification_id} {channel_name} dispatched successfully")

    def GetNotificationStatus(self, request, context):
        """Get the status of a previously sent notification"""
        span = trace.get_current_span()
        span.set_attribute("app.notification.id", request.notification_id)

        response = demo_pb2.GetNotificationStatusResponse()
        response.notification_id = request.notification_id

        notification_data = notification_statuses.get(request.notification_id)
        if not notification_data:
            logger.warning(f"Notification {request.notification_id} not found")
            return response

        for channel_name, channel_data in notification_data["channels"].items():
            channel_status = demo_pb2.NotificationChannelStatus()

            # Map channel name back to enum
            if channel_name == "EMAIL":
                channel_status.channel = demo_pb2.EMAIL
            elif channel_name == "SMS":
                channel_status.channel = demo_pb2.SMS
            elif channel_name == "WEBHOOK":
                channel_status.channel = demo_pb2.WEBHOOK
            elif channel_name == "PUSH":
                channel_status.channel = demo_pb2.PUSH

            channel_status.status = channel_data["status"]
            channel_status.error_message = channel_data["error_message"]
            response.channel_statuses.append(channel_status)

        logger.info(f"GetNotificationStatus for {request.notification_id}: {len(response.channel_statuses)} channels")
        return response

    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING)

    def Watch(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.UNIMPLEMENTED)


def must_map_env(key: str):
    value = os.environ.get(key)
    if value is None:
        raise Exception(f'{key} environment variable must be set')
    return value


def check_feature_flag(flag_name: str):
    """Check if a feature flag is enabled"""
    client = api.get_client()
    return client.get_boolean_value(flag_name, False)


if __name__ == "__main__":
    service_name = must_map_env('OTEL_SERVICE_NAME')
    api.set_provider(FlagdProvider(host=os.environ.get('FLAGD_HOST', 'flagd'), port=os.environ.get('FLAGD_PORT', 8013)))
    api.add_hooks([TracingHook()])

    # Initialize Traces and Metrics
    tracer = trace.get_tracer_provider().get_tracer(service_name)
    meter = metrics.get_meter_provider().get_meter(service_name)

    # Initialize Logs
    logger_provider = LoggerProvider(
        resource=Resource.create(
            {
                'service.name': service_name,
            }
        ),
    )
    set_logger_provider(logger_provider)
    log_exporter = OTLPLogExporter(insecure=True)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)

    # Attach OTLP handler to logger
    logger = logging.getLogger('main')
    logger.addHandler(handler)

    # Create gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Add class to gRPC server
    service = NotificationService()
    demo_pb2_grpc.add_NotificationServiceServicer_to_server(service, server)
    health_pb2_grpc.add_HealthServicer_to_server(service, server)

    # Start server
    port = must_map_env('NOTIFICATION_PORT')
    server.add_insecure_port(f'[::]:{port}')
    server.start()
    logger.info(f'Notification service started, listening on port {port}')
    server.wait_for_termination()
