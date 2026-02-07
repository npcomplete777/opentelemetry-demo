#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


# Python
import os
import random
import time
import uuid
from concurrent import futures

# Pip
import grpc
from opentelemetry import trace, metrics
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from openfeature import api
from openfeature.contrib.provider.flagd import FlagdProvider
from openfeature.contrib.hook.opentelemetry import TracingHook

# Local
import logging
import demo_pb2
import demo_pb2_grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

# Product IDs from the catalog
PRODUCT_IDS = [
    "OLJCESPC7Z", "66VCHSJNUP", "1YMWWN1N4O", "L9ECAV7KIM",
    "2ZYFJ3GM2N", "0PUK6V6EV0", "LS4PSXUNUM", "9SIQT8TOJO",
    "6E92ZMYYFZ"
]

# Sample user names for reviews
USER_NAMES = [
    "Alice Johnson", "Bob Smith", "Carol White", "David Brown",
    "Eve Davis", "Frank Miller", "Grace Wilson", "Henry Moore",
    "Iris Taylor", "Jack Anderson", "Karen Thomas", "Leo Jackson"
]

# Review templates
REVIEW_TITLES = [
    "Amazing product!",
    "Great quality",
    "Exceeded expectations",
    "Good value for money",
    "Highly recommend",
    "Pretty good",
    "Decent purchase",
    "Could be better",
    "Not what I expected",
    "Disappointing quality"
]

REVIEW_BODIES = [
    "This product has been fantastic for stargazing. Highly recommended!",
    "Excellent build quality and very easy to use.",
    "Perfect for beginners and enthusiasts alike.",
    "The optics are crystal clear and the setup was straightforward.",
    "A bit pricey but worth every penny for the quality.",
    "Good product overall, though the instructions could be clearer.",
    "Works well but I've seen better alternatives.",
    "Adequate for the price point, nothing exceptional.",
    "Had some issues with assembly but customer service helped.",
    "Not as advertised, expected better quality for this price."
]

# In-memory review store
reviews_store = {}


def initialize_reviews():
    """Pre-populate the store with fake reviews for each product"""
    for product_id in PRODUCT_IDS:
        num_reviews = random.randint(5, 15)
        product_reviews = []

        for _ in range(num_reviews):
            review_id = str(uuid.uuid4())
            user_id = f"user_{random.randint(1000, 9999)}"
            user_display_name = random.choice(USER_NAMES)
            rating = random.choices([1, 2, 3, 4, 5], weights=[1, 2, 5, 10, 15])[0]
            title = random.choice(REVIEW_TITLES)
            body = random.choice(REVIEW_BODIES)
            created_at = int(time.time()) - random.randint(0, 86400 * 90)  # Random time in last 90 days

            product_reviews.append({
                "review_id": review_id,
                "product_id": product_id,
                "user_id": user_id,
                "user_display_name": user_display_name,
                "rating": rating,
                "title": title,
                "body": body,
                "created_at": created_at
            })

        reviews_store[product_id] = product_reviews
        logger.info(f"Initialized {len(product_reviews)} reviews for product {product_id}")


class ReviewService(demo_pb2_grpc.ReviewServiceServicer):
    def SubmitReview(self, request, context):
        """Submit a new review"""
        span = trace.get_current_span()
        span.set_attribute("app.review.product_id", request.product_id)
        span.set_attribute("app.review.rating", request.rating)
        span.set_attribute("app.review.user_id", request.user_id)

        review_id = str(uuid.uuid4())

        # Get random display name for user
        user_display_name = random.choice(USER_NAMES)

        review = {
            "review_id": review_id,
            "product_id": request.product_id,
            "user_id": request.user_id,
            "user_display_name": user_display_name,
            "rating": request.rating,
            "title": request.title,
            "body": request.body,
            "created_at": int(time.time())
        }

        if request.product_id not in reviews_store:
            reviews_store[request.product_id] = []

        reviews_store[request.product_id].append(review)
        logger.info(f"Submitted review {review_id} for product {request.product_id}")

        response = demo_pb2.SubmitReviewResponse()
        response.review_id = review_id
        response.success = True
        return response

    def ListProductReviews(self, request, context):
        """Get reviews for a product - with optional N+1 anti-pattern"""
        span = trace.get_current_span()
        span.set_attribute("app.review.product_id", request.product_id)

        # Get reviews from store
        product_reviews = reviews_store.get(request.product_id, [])

        # Apply limit
        limit = request.limit if request.limit > 0 else 10
        limited_reviews = product_reviews[:limit]

        span.set_attribute("app.review.count", len(limited_reviews))

        # Check feature flag for N+1 pattern
        if check_feature_flag("reviewServiceNPlusOne"):
            span.set_attribute("app.review.mode", "n_plus_one")
            logger.info(f"GetProductReviews: N+1 mode enabled, fetching {len(limited_reviews)} user profiles individually")
            return self._get_reviews_n_plus_one(request, limited_reviews, span)
        else:
            span.set_attribute("app.review.mode", "normal")
            logger.info(f"GetProductReviews: normal mode (efficient)")
            return self._get_reviews_normal(request, limited_reviews)

    def _get_reviews_normal(self, request, product_reviews):
        """Normal mode - return reviews directly from store"""
        response = demo_pb2.ListProductReviewsResponse()

        for review_data in product_reviews:
            review = demo_pb2.ReviewItem()
            review.review_id = review_data["review_id"]
            review.product_id = review_data["product_id"]
            review.user_id = review_data["user_id"]
            review.user_display_name = review_data["user_display_name"]
            review.rating = review_data["rating"]
            review.title = review_data["title"]
            review.body = review_data["body"]
            review.created_at = review_data["created_at"]
            response.reviews.append(review)

        # Calculate average rating
        if product_reviews:
            total_rating = sum(r["rating"] for r in product_reviews)
            response.average_rating = total_rating / len(product_reviews)
        else:
            response.average_rating = 0.0

        response.total_count = len(reviews_store.get(request.product_id, []))

        return response

    def _get_reviews_n_plus_one(self, request, product_reviews, parent_span):
        """N+1 mode - fetch product catalog for each review to simulate user profile lookup"""
        response = demo_pb2.ListProductReviewsResponse()

        for idx, review_data in enumerate(product_reviews):
            # Simulate fetching user profile by calling GetProduct
            # This creates the N+1 "Comb" pattern
            # In a real scenario, this would be GetUserProfile, but we use GetProduct
            # as a stand-in since we don't have a user service
            try:
                # The gRPC call will be auto-instrumented by opentelemetry-instrument
                # so we don't need to create manual child spans
                product_request = demo_pb2.GetProductRequest(id=review_data["product_id"])
                product_catalog_stub.GetProduct(product_request)
                logger.debug(f"N+1: fetched 'user profile' (product) for review {idx}")
            except grpc.RpcError as e:
                logger.error(f"N+1: failed to fetch product for review {idx}: {e}")

            # Add the review to response
            review = demo_pb2.ReviewItem()
            review.review_id = review_data["review_id"]
            review.product_id = review_data["product_id"]
            review.user_id = review_data["user_id"]
            review.user_display_name = review_data["user_display_name"]
            review.rating = review_data["rating"]
            review.title = review_data["title"]
            review.body = review_data["body"]
            review.created_at = review_data["created_at"]
            response.reviews.append(review)

        # Calculate average rating
        if product_reviews:
            total_rating = sum(r["rating"] for r in product_reviews)
            response.average_rating = total_rating / len(product_reviews)
        else:
            response.average_rating = 0.0

        response.total_count = len(reviews_store.get(request.product_id, []))

        return response

    def GetRating(self, request, context):
        """Get average rating for a product"""
        span = trace.get_current_span()
        span.set_attribute("app.review.product_id", request.product_id)

        product_reviews = reviews_store.get(request.product_id, [])

        response = demo_pb2.GetRatingResponse()
        response.product_id = request.product_id
        response.review_count = len(product_reviews)

        if product_reviews:
            total_rating = sum(r["rating"] for r in product_reviews)
            response.average_rating = total_rating / len(product_reviews)
        else:
            response.average_rating = 0.0

        logger.info(f"GetProductRating for {request.product_id}: {response.average_rating:.2f} ({response.review_count} reviews)")

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

    # Initialize review data
    initialize_reviews()

    # Connect to product catalog service (used for N+1 simulation)
    catalog_addr = must_map_env('PRODUCT_CATALOG_ADDR')
    pc_channel = grpc.insecure_channel(catalog_addr)
    product_catalog_stub = demo_pb2_grpc.ProductCatalogServiceStub(pc_channel)

    # Create gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Add class to gRPC server
    service = ReviewService()
    demo_pb2_grpc.add_ReviewServiceServicer_to_server(service, server)
    health_pb2_grpc.add_HealthServicer_to_server(service, server)

    # Start server
    port = must_map_env('REVIEW_PORT')
    server.add_insecure_port(f'[::]:{port}')
    server.start()
    logger.info(f'Review service started, listening on port {port}')
    server.wait_for_termination()
