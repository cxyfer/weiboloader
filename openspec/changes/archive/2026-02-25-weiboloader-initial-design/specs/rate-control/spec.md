## ADDED Requirements

### Requirement: Sliding window rate limiter for API requests
The system SHALL enforce a sliding window rate limit of 30 requests per 600 seconds (10 minutes) for API requests to `m.weibo.cn`. The rate controller MUST be implemented as an independent module (`ratecontrol.py`) with a subclassable base class.

#### Scenario: Proactive throttle within window
- **WHEN** 30 API requests have been made in the last 600 seconds
- **THEN** system SHALL block the next API request until the oldest request exits the window

#### Scenario: Burst at window boundary
- **WHEN** 29 requests were made at t=0s and 1 request at t=599s
- **THEN** system SHALL allow the 30th request at t=599s and block the 31st until t=600s

#### Scenario: Custom request interval
- **WHEN** user passes `--request-interval 5`
- **THEN** system SHALL enforce a minimum 5-second gap between consecutive API requests, in addition to the sliding window limit

### Requirement: Separate media download rate window
API requests and media download requests MUST use independent rate windows. Consuming API quota SHALL NOT affect media download quota, and vice versa.

#### Scenario: Interleaved API and media requests
- **WHEN** 30 API requests have been made (window full) and a media download is requested
- **THEN** system SHALL allow the media download without waiting for API window to clear

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

<!-- PBT: ∀ 600s window, Count(API requests) ≤ 30 -->
<!-- PBT: API quota and media quota SHALL NOT cross-pollute -->
<!-- PBT: Backoff delay[k+1] >= Backoff delay[k] (excluding jitter) -->
