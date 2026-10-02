#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Python
import os
import simplejson as json
import logging

# Postgres
import psycopg2
from psycopg2 import pool

def must_map_env(key: str):
    value = os.environ.get(key)
    if value is None:
        raise Exception(f'{key} environment variable must be set')
    return value

# Retrieve Postgres environment variables
db_connection_str = must_map_env('DB_CONNECTION_STRING')

# Initialize connection pool (min 2, max 10 connections)
connection_pool = pool.ThreadedConnectionPool(
    minconn=2,
    maxconn=10,
    dsn=db_connection_str
)

def fetch_product_reviews(product_id):
    try:
        return json.dumps(fetch_product_reviews_from_db(product_id), use_decimal=True)
    except Exception as e:
        return json.dumps({"error": str(e)})

def fetch_product_reviews_from_db(request_product_id):
    conn = connection_pool.getconn()

    try:
        with conn.cursor() as cursor:
            # Define the SQL query
            query = "SELECT username, description, score FROM reviews.productreviews WHERE product_id= %s"

            # Execute the query
            cursor.execute(query, (request_product_id, ))

            # Fetch all the rows from the query result
            records = cursor.fetchall()
            return records

    except Exception as e:
        raise e
    finally:
        # Return connection to pool (not close!)
        connection_pool.putconn(conn)

def fetch_avg_product_review_score_from_db(request_product_id):
    conn = connection_pool.getconn()

    try:
        with conn.cursor() as cursor:
            # Define the SQL query
            query = "SELECT AVG(score) FROM reviews.productreviews WHERE product_id= %s"

            # Execute the query
            cursor.execute(query, (request_product_id, ))

            # Fetch all the rows from the query result
            records = cursor.fetchall()

            # Extract the average score
            if records:
                # records will be a list like [(average_score,)]
                average_score = records[0][0]
            else:
                # Handle the case where no records are returned (e.g., no reviews for the product)
                average_score = None

            # return the score as a string rounded to 1 decimal place
            return f"{average_score:.1f}"

    except Exception as e:
        raise e
    finally:
        # Return connection to pool (not close!)
        connection_pool.putconn(conn)

def verify_pool_health():
    """Verify pool is healthy on startup."""
    logger = logging.getLogger('main')
    conn = connection_pool.getconn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
        logger.info("Database connection pool verified healthy")
    finally:
        connection_pool.putconn(conn)

# Call at module load
verify_pool_health()
