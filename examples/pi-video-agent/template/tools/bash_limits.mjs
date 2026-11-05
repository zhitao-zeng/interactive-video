// Bounded waits for the native bash tool.
//
// The SDK's bash schema has an optional `timeout` and, when it is absent, installs no timer at
// all, so a single careless command can outlive the whole run. S2E03A lost 3h19m to one
// `find /` with no timeout and stopped at the wall deadline with no film. Real long operations in
// this project always pass an explicit timeout, so a default only ever catches the careless case.
//
// Two rules, both applied here so the caller stays a one-liner:
//   1. no explicit timeout -> a bounded default;
//   2. any timeout (explicit or default) may not exceed the wall clock this run has left.
export const DEFAULT_BASH_TIMEOUT_SECONDS = 900;

/** Whole seconds left before the run's wall deadline; never below 1 so a call can still report. */
export function remainingSeconds(deadlineMs, nowMs = Date.now()) {
  const left = (Number(deadlineMs) - nowMs) / 1000;
  if (!Number.isFinite(left)) return 1;
  return Math.max(1, Math.floor(left));
}

/**
 * Effective timeout in seconds for one bash call.
 * Returns an integer >= 1 and never above `remainingSeconds`.
 */
export function resolveBashTimeout({ requested, remainingSeconds: remaining, defaultSeconds = DEFAULT_BASH_TIMEOUT_SECONDS }) {
  const cap = Math.max(1, Math.floor(Number(remaining) || 1));
  const fallback = Math.max(1, Math.floor(Number(defaultSeconds) || DEFAULT_BASH_TIMEOUT_SECONDS));
  const wanted = Number.isFinite(Number(requested)) && Number(requested) > 0
    ? Math.floor(Number(requested))
    : fallback;
  return Math.max(1, Math.min(wanted, cap));
}

/** Whether a timeout came from the caller or from the bounded default. */
export function timeoutSource(requested) {
  return Number.isFinite(Number(requested)) && Number(requested) > 0 ? 'explicit' : 'default';
}
