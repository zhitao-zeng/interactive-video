// Fast regression for the bounded-wait wiring.
import { resolveBashTimeout, remainingSeconds, timeoutSource, DEFAULT_BASH_TIMEOUT_SECONDS } from './bash_limits.mjs';

let fail = 0;
function eq(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { console.log(`FAIL ${label}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`); fail++; }
  else console.log(`ok   ${label} = ${JSON.stringify(got)}`);
}

// 1. No timeout given -> bounded default (the S2E03A case).
eq('no timeout -> default', resolveBashTimeout({ requested: undefined, remainingSeconds: 15356 }), DEFAULT_BASH_TIMEOUT_SECONDS);
eq('null timeout -> default', resolveBashTimeout({ requested: null, remainingSeconds: 15356 }), DEFAULT_BASH_TIMEOUT_SECONDS);
eq('zero timeout -> default', resolveBashTimeout({ requested: 0, remainingSeconds: 15356 }), DEFAULT_BASH_TIMEOUT_SECONDS);
eq('source(undefined)', timeoutSource(undefined), 'default');

// 2. Explicit timeout below remaining wall clock is honoured unchanged.
eq('explicit 2100 under 15000 left', resolveBashTimeout({ requested: 2100, remainingSeconds: 15000 }), 2100);
eq('explicit 600 under 900 left', resolveBashTimeout({ requested: 600, remainingSeconds: 900 }), 600);
eq('source(2100)', timeoutSource(2100), 'explicit');

// 3. Explicit timeout beyond remaining wall clock is clamped down, never up.
eq('explicit 1600 with 300 left', resolveBashTimeout({ requested: 1600, remainingSeconds: 300 }), 300);
eq('default capped by 120 left', resolveBashTimeout({ requested: undefined, remainingSeconds: 120 }), 120);

// 4. Never returns < 1 even past the deadline, so the call can still report.
eq('remaining 0 -> 1', resolveBashTimeout({ requested: 600, remainingSeconds: 0 }), 1);
eq('remaining negative -> 1', resolveBashTimeout({ requested: 600, remainingSeconds: -50 }), 1);
eq('remainingSeconds past deadline', remainingSeconds(Date.now() - 60000), 1);

// 5. Default itself cannot exceed remaining time.
eq('default clamped by 60', resolveBashTimeout({ requested: undefined, remainingSeconds: 60 }), 60);

// 6. Explicit value equal to remaining stays equal.
eq('explicit == remaining', resolveBashTimeout({ requested: 900, remainingSeconds: 900 }), 900);

console.log(fail === 0 ? 'ALL PASS' : `${fail} FAILED`);
process.exit(fail === 0 ? 0 : 1);
