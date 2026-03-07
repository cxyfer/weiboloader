## ADDED Requirements

### Requirement: Sliding window rate limiter for API requests
The system SHALL enforce a sliding window rate limit of 60 requests per 600 seconds for API requests to `m.weibo.cn`. The default quota MUST be configurable from the CLI via `--api-rate-limit` and `--api-rate-window`. The rate controller MUST be implemented as an independent module (`ratecontrol.py`) with a subclassable base class.

#### Scenario: Default API quota
- **WHEN** user does not pass `--api-rate-limit` or `--api-rate-window`
- **THEN** system SHALL enforce the default API quota of 60 requests per 600 seconds

#### Scenario: Custom API quota from CLI
- **WHEN** user passes `--api-rate-limit 30 --api-rate-window 120`
- **THEN** system SHALL enforce a sliding-window API quota of 30 requests per 120 seconds

#### Scenario: Proactive throttle within window
- **WHEN** 60 API requests have been made in the last 600 seconds
- **THEN** system SHALL block the next API request until the oldest request exits the window

#### Scenario: Burst at window boundary
- **WHEN** 59 requests were made at t=0s and 1 request at t=599s
- **THEN** system SHALL allow the 60th request at t=599s and block the 61st until t=600s

#### Scenario: Custom request interval
- **WHEN** user passes `--request-interval 5`
- **THEN** system SHALL enforce a minimum 5-second gap between consecutive requests within each bucket, in addition to the API sliding window limit

### Requirement: Separate media request pacing
API requests and media requests MUST use independent pacing state. Consuming API quota SHALL NOT affect media request pacing, and vice versa. Media requests MUST NOT use the sliding-window quota, but they MUST still honor `--request-interval` and reactive backoff independently from API requests.

#### Scenario: Interleaved API and media requests
- **WHEN** 60 API requests have been made (window full) and a media download is requested
- **THEN** system SHALL allow the media download without waiting for the API window to clear

#### Scenario: Media request interval remains independent
- **WHEN** user passes `--request-interval 5` and two consecutive media requests are made
- **THEN** system SHALL enforce a minimum 5-second gap between those media requests without consuming API sliding-window quota

### Requirement: Reactive exponential backoff on 403/418
When the server responds with HTTP 403 or 418, the system SHALL apply exponential backoff with jitter before retrying.

#### Scenario: First 403 response
- **WHEN** server returns HTTP 403 for the first time
- **THEN** system SHALL wait `base_delay * 2^0 + jitter` before retrying

#### Scenario: Consecutive 403 responses
- **WHEN** server returns HTTP 403 for the k-th consecutive time
- **THEN** system SHALL wait `base_delay * 2^(k-1) + jitter`, and the base delay (excluding jitter) MUST be monotonically non-decreasing

#### Scenario: Backoff reset on success
- **WHEN** a request succeeds after previous 403/418 failures
- **THEN** system SHALL reset the backoff counter to 0

### Requirement: Subclassable rate controller
The `RateController` base class SHALL expose `wait_before_request(bucket)` and `handle_response(bucket, status_code)` methods that can be overridden by users to implement custom rate limiting strategies.

#### Scenario: Custom rate controller
- **WHEN** user subclasses `RateController` and overrides `wait_before_request`
- **THEN** the custom logic SHALL be invoked for every request

<!-- PBT: ∀ 600s window, Count(API requests) ≤ 60 -->
<!-- PBT: API quota SHALL NOT affect media pacing -->
<!-- PBT: Backoff delay[k+1] >= Backoff delay[k] (excluding jitter) -->
