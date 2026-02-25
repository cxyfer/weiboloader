## ADDED Requirements

### Requirement: Playwright CAPTCHA handler (optional dependency)
When the system detects a CAPTCHA challenge (HTTP 418 or specific redirect pattern), it SHALL attempt to launch Playwright chromium to let the user manually solve the CAPTCHA. Playwright MUST be an optional dependency (`pip install weiboloader[captcha]`).

#### Scenario: CAPTCHA detected with Playwright installed
- **WHEN** server returns HTTP 418 or a CAPTCHA redirect and Playwright is installed
- **THEN** system SHALL launch a Playwright chromium window, navigate to the verification page, and wait for user to complete CAPTCHA

#### Scenario: CAPTCHA solved successfully
- **WHEN** user completes CAPTCHA in the Playwright window
- **THEN** system SHALL extract updated cookies from the browser and continue downloading

#### Scenario: CAPTCHA timeout
- **WHEN** user does not complete CAPTCHA within 300 seconds
- **THEN** system SHALL abort the current target gracefully and proceed to the next target in the batch

### Requirement: Fallback pause-and-wait mode
When Playwright is not installed, the system SHALL fall back to a pause-and-wait mode that prints the verification URL and waits for user confirmation.

#### Scenario: Playwright not installed
- **WHEN** CAPTCHA is triggered and Playwright is not installed
- **THEN** system SHALL print the verification URL, pause execution, and wait for user to press Enter after manually completing CAPTCHA

#### Scenario: Fallback timeout
- **WHEN** user does not confirm within 300 seconds in pause-and-wait mode
- **THEN** system SHALL abort the current target and continue with the next

### Requirement: Configurable CAPTCHA mode
The system SHALL support a `--captcha-mode` flag with values: `auto` (default, use Playwright if available), `browser` (force Playwright), `manual` (force pause-and-wait), `skip` (abort target immediately on CAPTCHA).

#### Scenario: Auto mode with Playwright available
- **WHEN** `--captcha-mode auto` and Playwright is installed
- **THEN** system SHALL use Playwright handler

#### Scenario: Auto mode without Playwright
- **WHEN** `--captcha-mode auto` and Playwright is not installed
- **THEN** system SHALL use pause-and-wait handler

#### Scenario: Skip mode
- **WHEN** `--captcha-mode skip` and CAPTCHA is triggered
- **THEN** system SHALL immediately abort the current target without waiting

### Requirement: CAPTCHA isolation from batch
A CAPTCHA timeout or failure on one target MUST NOT interrupt processing of other targets in the batch.

#### Scenario: CAPTCHA failure mid-batch
- **WHEN** target 2 of 5 triggers CAPTCHA and times out
- **THEN** system SHALL report failure for target 2 and continue processing targets 3, 4, 5

<!-- PBT: CAPTCHA state machine: INIT → WAITING → SOLVED|TIMEOUT (no reverse transitions) -->
<!-- PBT: CAPTCHA total duration ≤ Config.Timeout (300s default) -->
