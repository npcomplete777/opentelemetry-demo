#!/usr/bin/env python3
"""
Geometry Lab Load Generator

Continuously calls all 7 Geometry Lab gRPC services with realistic payloads
to generate traces for anti-pattern detection testing.
"""

import grpc
import time
import random
import sys
from datetime import datetime

# Import generated protobuf stubs
import demo_pb2
import demo_pb2_grpc


# Service endpoints
SERVICES = {
    "inventory": "inventory:8080",
    "review": "review:8080",
    "notification": "notification:8080",
    "search": "search:8080",
    "user": "user:8080",
    "order": "order:8080",
    "pricing": "pricing:8080"
}

# Sample product IDs from the OTel demo
PRODUCT_IDS = [
    "OLJCESPC7Z",  # Sunglasses
    "66VCHSJNUP",  # Tank Top
    "1YMWWN1N4O",  # Home Barista Kit
    "L9ECAV7KIM",  # Terrarium
    "2ZYFJ3GM2N",  # Film Camera
    "0PUK6V6EV0",  # Vintage Typewriter
    "LS4PSXUNUM",  # Metal Camping Mug
    "9SIQT8TOJO",  # City Bike
    "6E92ZMYYFZ",  # Air Plant
]

USER_IDS = ["user-123", "user-456", "user-789", "user-abc", "user-xyz"]


def call_inventory_service():
    """Call InventoryService.ReserveStock"""
    try:
        with grpc.insecure_channel(SERVICES["inventory"]) as channel:
            stub = demo_pb2_grpc.InventoryServiceStub(channel)

            # Create a realistic stock reservation request
            items = [
                demo_pb2.StockItem(
                    product_id=random.choice(PRODUCT_IDS),
                    quantity=random.randint(1, 3)
                )
                for _ in range(random.randint(1, 4))
            ]

            request = demo_pb2.ReserveStockRequest(
                order_id=f"order-{int(time.time())}",
                items=items
            )

            response = stub.ReserveStock(request, timeout=10)
            print(f"✓ inventory    | ReserveStock | success={response.success} | items={len(items)}")
            return True
    except grpc.RpcError as e:
        print(f"✗ inventory    | ReserveStock | ERROR: {e.code().name}")
        return False
    except Exception as e:
        print(f"✗ inventory    | ReserveStock | ERROR: {str(e)[:60]}")
        return False


def call_review_service():
    """Call ReviewService.ListProductReviews"""
    try:
        with grpc.insecure_channel(SERVICES["review"]) as channel:
            stub = demo_pb2_grpc.ReviewServiceStub(channel)

            request = demo_pb2.ListProductReviewsRequest(
                product_id=random.choice(PRODUCT_IDS),
                limit=10
            )

            response = stub.ListProductReviews(request, timeout=10)
            print(f"✓ review       | ListProductReviews | reviews={len(response.reviews)} | avg_rating={response.average_rating:.1f}")
            return True
    except grpc.RpcError as e:
        print(f"✗ review       | ListProductReviews | ERROR: {e.code().name}")
        return False
    except Exception as e:
        print(f"✗ review       | ListProductReviews | ERROR: {str(e)[:60]}")
        return False


def call_notification_service():
    """Call NotificationService.SendNotification"""
    try:
        with grpc.insecure_channel(SERVICES["notification"]) as channel:
            stub = demo_pb2_grpc.NotificationServiceStub(channel)

            request = demo_pb2.SendNotificationRequest(
                user_id=random.choice(USER_IDS),
                order_id=f"order-{int(time.time())}",
                type=demo_pb2.ORDER_CONFIRMATION,
                subject="Order Confirmation",
                body="Your order has been confirmed!",
                channels=[demo_pb2.EMAIL, demo_pb2.SMS]
            )

            response = stub.SendNotification(request, timeout=10)
            print(f"✓ notification | SendNotification | accepted={response.accepted} | id={response.notification_id[:16]}...")
            return True
    except grpc.RpcError as e:
        print(f"✗ notification | SendNotification | ERROR: {e.code().name}")
        return False
    except Exception as e:
        print(f"✗ notification | SendNotification | ERROR: {str(e)[:60]}")
        return False


def call_search_service():
    """Call SearchService.SearchProducts"""
    try:
        with grpc.insecure_channel(SERVICES["search"]) as channel:
            stub = demo_pb2_grpc.SearchServiceStub(channel)

            queries = ["vintage", "camping", "bike", "camera", "plant"]

            request = demo_pb2.SearchRequest(
                query=random.choice(queries),
                max_results=10,
                sort_by="relevance"
            )

            response = stub.SearchProducts(request, timeout=10)
            print(f"✓ search       | SearchProducts | results={len(response.results)} | duration={response.search_duration_ms:.1f}ms")
            return True
    except grpc.RpcError as e:
        print(f"✗ search       | SearchProducts | ERROR: {e.code().name}")
        return False
    except Exception as e:
        print(f"✗ search       | SearchProducts | ERROR: {str(e)[:60]}")
        return False


def call_user_service():
    """Call UserService.GetUserProfile"""
    try:
        with grpc.insecure_channel(SERVICES["user"]) as channel:
            stub = demo_pb2_grpc.UserServiceStub(channel)

            request = demo_pb2.GetUserProfileRequest(
                user_id=random.choice(USER_IDS)
            )

            response = stub.GetUserProfile(request, timeout=10)
            print(f"✓ user         | GetUserProfile | user={response.profile.user_id} | name={response.profile.display_name}")
            return True
    except grpc.RpcError as e:
        print(f"✗ user         | GetUserProfile | ERROR: {e.code().name}")
        return False
    except Exception as e:
        print(f"✗ user         | GetUserProfile | ERROR: {str(e)[:60]}")
        return False


def call_order_service():
    """Call OrderService.CreateOrder"""
    try:
        with grpc.insecure_channel(SERVICES["order"]) as channel:
            stub = demo_pb2_grpc.OrderServiceStub(channel)

            # Create a minimal order request
            address = demo_pb2.Address(
                street_address="123 Main St",
                city="Portland",
                state="OR",
                country="USA",
                zip_code=97201
            )

            card = demo_pb2.CreditCardInfo(
                credit_card_number="4111-1111-1111-1111",
                credit_card_cvv=123,
                credit_card_expiration_year=2025,
                credit_card_expiration_month=12
            )

            request = demo_pb2.CreateOrderRequest(
                user_id=random.choice(USER_IDS),
                cart_id=f"cart-{int(time.time())}",
                currency_code="USD",
                shipping_address=address,
                credit_card=card
            )

            response = stub.CreateOrder(request, timeout=10)
            print(f"✓ order        | CreateOrder | order_id={response.order_id[:16]}... | status={response.status}")
            return True
    except grpc.RpcError as e:
        print(f"✗ order        | CreateOrder | ERROR: {e.code().name}")
        return False
    except Exception as e:
        print(f"✗ order        | CreateOrder | ERROR: {str(e)[:60]}")
        return False


def call_pricing_service():
    """Call PricingService.GetBulkPrices"""
    try:
        with grpc.insecure_channel(SERVICES["pricing"]) as channel:
            stub = demo_pb2_grpc.PricingServiceStub(channel)

            # Request prices for a random set of products
            product_count = random.randint(5, 20)
            product_ids = random.sample(PRODUCT_IDS, min(product_count, len(PRODUCT_IDS)))

            request = demo_pb2.GetBulkPricesRequest(
                product_ids=product_ids,
                currency_code="USD"
            )

            response = stub.GetBulkPrices(request, timeout=10)
            print(f"✓ pricing      | GetBulkPrices | products={len(response.prices)} | requested={len(product_ids)}")
            return True
    except grpc.RpcError as e:
        print(f"✗ pricing      | GetBulkPrices | ERROR: {e.code().name}")
        return False
    except Exception as e:
        print(f"✗ pricing      | GetBulkPrices | ERROR: {str(e)[:60]}")
        return False


SERVICE_CALLERS = [
    ("inventory", call_inventory_service),
    ("review", call_review_service),
    ("notification", call_notification_service),
    ("search", call_search_service),
    ("user", call_user_service),
    ("order", call_order_service),
    ("pricing", call_pricing_service),
]


def main():
    """Main load generation loop"""
    print("=" * 100)
    print("Geometry Lab Load Generator")
    print("Calling all 7 Geometry Lab gRPC services in continuous loop")
    print("=" * 100)
    print()

    iteration = 0
    total_success = 0
    total_calls = 0

    try:
        while True:
            iteration += 1
            print(f"\n[Iteration {iteration}] {datetime.now().isoformat()}")
            print("-" * 100)

            round_success = 0
            round_calls = 0

            # Call each service once per iteration
            for service_name, caller_func in SERVICE_CALLERS:
                if caller_func():
                    round_success += 1
                round_calls += 1

                # Small delay between calls
                time.sleep(random.uniform(0.3, 0.8))

            total_success += round_success
            total_calls += round_calls

            success_rate = (total_success / total_calls * 100) if total_calls > 0 else 0

            print("-" * 100)
            print(f"Round: {round_success}/{round_calls} | Total: {total_success}/{total_calls} ({success_rate:.1f}%)")

            # Wait before next iteration (5-10 seconds)
            delay = random.uniform(5, 10)
            print(f"Next iteration in {delay:.1f}s...")
            time.sleep(delay)

    except KeyboardInterrupt:
        print("\n\nShutting down gracefully...")
        print(f"Final stats: {total_success}/{total_calls} successful calls ({success_rate:.1f}%)")
        sys.exit(0)


if __name__ == "__main__":
    main()
