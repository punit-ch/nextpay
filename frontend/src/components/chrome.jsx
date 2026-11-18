// Page chrome: wordmark, top bar, progress tracker and footer.

export function SlipGlyph({ className = "" }) {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 22 22"
      aria-hidden="true"
      className={className}
    >
      <path
        d="M3 2h16v15l-2.67 3L13.67 17 11 20l-2.67-3L5.67 20 3 17z"
        fill="currentColor"
      />
      <path
        d="M7 7h8M7 11h5"
        stroke="#0b1124"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function Wordmark() {
  return (
    <a href="/" className="flex items-center gap-2.5 text-fg">
      <SlipGlyph className="text-accent" />
      <span className="font-serif text-lg font-bold tracking-[0.06em]">
        NEXTPAY
      </span>
    </a>
  );
}

export function TopBar() {
  return (
    <header className="border-b border-line">
      <div className="mx-auto flex max-w-[1180px] items-center justify-between px-5 py-4 sm:px-8">
        <Wordmark />
        <p className="flex items-center gap-2 text-sm text-muted">
          <span aria-hidden="true" className="size-2 rounded-full bg-ok" />
          <span>
            <span className="hidden sm:inline">PayPal Sandbox: </span>
            no real money moves
          </span>
        </p>
      </div>
    </header>
  );
}

const STEPS = ["Describe", "Review", "Approve"];

/** Three segments; the current and finished steps are filled. */
export function StepTracker({ step }) {
  return (
    <ol className="grid grid-cols-3 gap-3" aria-label="Progress">
      {STEPS.map((label, index) => {
        const number = index + 1;
        const done = number < step;
        const current = number === step;

        return (
          <li key={label} aria-current={current ? "step" : undefined}>
            <span
              aria-hidden="true"
              className={`block h-[3px] rounded-full ${
                done || current ? "bg-accent" : "bg-line"
              }`}
            />
            <span
              className={`mt-2 flex items-baseline gap-2 text-sm ${
                done || current ? "text-fg" : "text-faint"
              }`}
            >
              <span className="font-serif tabular-nums text-muted">
                0{number}
              </span>
              {label}
              {done && <span className="sr-only">(done)</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-line">
      <p className="mx-auto max-w-[1180px] px-5 py-8 text-sm text-faint sm:px-8">
        NEXTPAY is a hackathon demo. The catalogue is sample data and payments
        run in PayPal Sandbox, so nothing real is ever charged.
      </p>
    </footer>
  );
}
