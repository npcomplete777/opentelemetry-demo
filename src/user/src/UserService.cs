// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Grpc.Core;
using Microsoft.Extensions.Logging;
using OpenTelemetry;
using OpenTelemetry.Trace;
using OpenFeature;
using Oteldemo;

namespace user.services;

public class UserService : Oteldemo.UserService.UserServiceBase
{
    private readonly ILogger<UserService> _logger;
    private readonly IFeatureClient _featureClient;
    private readonly ActivitySource _activitySource;

    // Simulated connection pool with max 3 connections
    private static readonly SemaphoreSlim _connectionPool = new(3, 3);

    // In-memory user database
    private static readonly Dictionary<string, UserProfile> _users = InitializeUsers();

    public UserService(ILogger<UserService> logger, IFeatureClient featureClient)
    {
        _logger = logger;
        _featureClient = featureClient;
        _activitySource = new ActivitySource("OpenTelemetry.Demo.User");
    }

    private static Dictionary<string, UserProfile> InitializeUsers()
    {
        var users = new Dictionary<string, UserProfile>();
        var names = new[] { "Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Heidi",
                           "Ivan", "Judy", "Mallory", "Oscar", "Peggy", "Sybil", "Trent", "Trudy",
                           "Victor", "Walter", "Zelda", "Wendy", "Xavier", "Yvonne", "Zara", "Arthur",
                           "Beatrice", "Charlie", "Diana", "Edward", "Fiona", "George", "Hannah",
                           "Isaac", "Julia", "Kevin", "Laura", "Michael", "Nina", "Oliver", "Paula",
                           "Quinn", "Rachel", "Samuel", "Tina", "Ulysses", "Vera", "William", "Xena",
                           "Yuri", "Zoe" };

        var currencies = new[] { "USD", "EUR", "GBP", "JPY", "CAD", "AUD" };
        var languages = new[] { "en", "es", "fr", "de", "ja", "zh" };

        for (int i = 0; i < names.Length; i++)
        {
            var userId = $"user-{i+1:D3}";
            users[userId] = new UserProfile
            {
                UserId = userId,
                DisplayName = names[i],
                Email = $"{names[i].ToLower()}@example.com",
                AvatarUrl = $"https://api.dicebear.com/7.x/avataaars/svg?seed={names[i]}",
                PreferredCurrency = currencies[i % currencies.Length],
                PreferredLanguage = languages[i % languages.Length],
                CreatedAt = DateTimeOffset.Now.AddDays(-Random.Shared.Next(1, 365)).ToUnixTimeSeconds()
            };
        }

        return users;
    }

    public override async Task<GetUserProfileResponse> GetUserProfile(
        GetUserProfileRequest request, ServerCallContext context)
    {
        var activity = Activity.Current;

        try
        {
            // Check if pool exhaustion mode is enabled
            var poolExhaustionEnabled = await _featureClient.GetBooleanValueAsync(
                "userServicePoolExhaustion", false, context: null);

            var mode = poolExhaustionEnabled ? "pool_exhaustion" : "normal";
            activity?.SetTag("app.pool.mode", mode);
            activity?.SetTag("app.user.id", request.UserId);

            _logger.LogInformation("GetUserProfile called for user {UserId}, mode: {Mode}",
                request.UserId, mode);

            if (poolExhaustionEnabled)
            {
                return await GetUserProfileWithPoolExhaustion(request, activity);
            }
            else
            {
                return await GetUserProfileNormal(request, activity);
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error in GetUserProfile");
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }

    private async Task<GetUserProfileResponse> GetUserProfileNormal(
        GetUserProfileRequest request, Activity? parentActivity)
    {
        // Normal mode: fast, direct lookup
        using var dbActivity = _activitySource.StartActivity("db.query", ActivityKind.Client);
        dbActivity?.SetTag("db.system", "simulated");
        dbActivity?.SetTag("db.operation", "SELECT");
        dbActivity?.SetTag("db.statement", "SELECT * FROM user_profiles WHERE user_id = ?");
        dbActivity?.SetTag("db.user", request.UserId);

        // Simulate normal DB query latency
        await Task.Delay(Random.Shared.Next(2, 5));

        if (!_users.TryGetValue(request.UserId, out var profile))
        {
            throw new RpcException(new Grpc.Core.Status(Grpc.Core.StatusCode.NotFound,
                $"User {request.UserId} not found"));
        }

        return new GetUserProfileResponse { Profile = profile };
    }

    private async Task<GetUserProfileResponse> GetUserProfileWithPoolExhaustion(
        GetUserProfileRequest request, Activity? parentActivity)
    {
        var poolAcquireStart = Stopwatch.StartNew();

        // Create pool.acquire span
        using var poolActivity = _activitySource.StartActivity("pool.acquire", ActivityKind.Internal);
        poolActivity?.SetTag("app.pool.max_size", 3);
        poolActivity?.SetTag("app.pool.available", _connectionPool.CurrentCount);

        parentActivity?.SetTag("app.pool.max_size", 3);

        // Try to acquire connection from pool with 5 second timeout
        bool acquired = await _connectionPool.WaitAsync(TimeSpan.FromMilliseconds(5000));

        poolAcquireStart.Stop();
        var waitMs = poolAcquireStart.ElapsedMilliseconds;

        poolActivity?.SetTag("app.pool.acquired", acquired);
        poolActivity?.SetTag("app.pool.wait_ms", waitMs);

        if (!acquired)
        {
            // Pool exhausted - timeout
            _logger.LogWarning("Connection pool timeout after {WaitMs}ms", waitMs);

            poolActivity?.SetStatus(ActivityStatusCode.Error, "Connection pool timeout");
            poolActivity?.SetTag("app.pool.outcome", "timeout");

            parentActivity?.SetStatus(ActivityStatusCode.Error, "Connection pool exhausted");
            parentActivity?.SetTag("app.pool.outcome", "timeout");
            parentActivity?.SetTag("app.pool.wait_ms", waitMs);

            throw new RpcException(new Grpc.Core.Status(Grpc.Core.StatusCode.ResourceExhausted,
                "Connection pool timeout - no connections available"));
        }

        poolActivity?.SetTag("app.pool.outcome", "acquired");

        try
        {
            // Successfully acquired connection
            _logger.LogInformation("Connection acquired after {WaitMs}ms", waitMs);

            parentActivity?.SetTag("app.pool.outcome", "acquired");
            parentActivity?.SetTag("app.pool.wait_ms", waitMs);

            // Simulate DB query that holds the connection for a long time
            using var dbActivity = _activitySource.StartActivity("db.query", ActivityKind.Client);
            dbActivity?.SetTag("db.system", "simulated");
            dbActivity?.SetTag("db.operation", "SELECT");
            dbActivity?.SetTag("db.statement", "SELECT * FROM user_profiles WHERE user_id = ?");
            dbActivity?.SetTag("db.user", request.UserId);
            dbActivity?.SetTag("app.pool.connection_held", true);

            // This is the anti-pattern: hold the connection for 500-1000ms
            // This causes the pool to drain under concurrent load
            var holdTime = Random.Shared.Next(500, 1000);
            dbActivity?.SetTag("app.pool.hold_time_ms", holdTime);
            await Task.Delay(holdTime);

            if (!_users.TryGetValue(request.UserId, out var profile))
            {
                throw new RpcException(new Grpc.Core.Status(Grpc.Core.StatusCode.NotFound,
                    $"User {request.UserId} not found"));
            }

            return new GetUserProfileResponse { Profile = profile };
        }
        finally
        {
            // Release connection back to pool
            _connectionPool.Release();
            _logger.LogDebug("Connection released back to pool");
        }
    }

    public override async Task<UpdatePreferencesResponse> UpdatePreferences(
        UpdatePreferencesRequest request, ServerCallContext context)
    {
        var activity = Activity.Current;
        activity?.SetTag("app.user.id", request.UserId);

        try
        {
            if (!_users.TryGetValue(request.UserId, out var profile))
            {
                throw new RpcException(new Grpc.Core.Status(Grpc.Core.StatusCode.NotFound,
                    $"User {request.UserId} not found"));
            }

            // Update preferences
            if (!string.IsNullOrEmpty(request.PreferredCurrency))
            {
                profile.PreferredCurrency = request.PreferredCurrency;
            }
            if (!string.IsNullOrEmpty(request.PreferredLanguage))
            {
                profile.PreferredLanguage = request.PreferredLanguage;
            }

            _logger.LogInformation("Updated preferences for user {UserId}", request.UserId);

            return new UpdatePreferencesResponse { Success = true };
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error updating preferences");
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }

    public override async Task<GetUserAddressResponse> GetUserAddress(
        GetUserAddressRequest request, ServerCallContext context)
    {
        var activity = Activity.Current;
        activity?.SetTag("app.user.id", request.UserId);

        try
        {
            if (!_users.TryGetValue(request.UserId, out var profile))
            {
                throw new RpcException(new Grpc.Core.Status(Grpc.Core.StatusCode.NotFound,
                    $"User {request.UserId} not found"));
            }

            // Return a fake address
            return new GetUserAddressResponse
            {
                Street = "123 Main St",
                City = "Anytown",
                State = "CA",
                Zip = "12345",
                Country = "US"
            };
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error getting user address");
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            throw;
        }
    }
}
