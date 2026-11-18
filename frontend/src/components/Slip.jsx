import {
  Check,
  CircleAlert,
  LoaderCircle,
  Minus,
  Plus,
  ShieldCheck,
} from "lucide-react";

// The purchase slip: the one light object on the dark desk. It fills in as
// the buyer chooses, and shows a server-confirmed total once a plan exists.

export function SlipEdge() {
  return <div className="slip-edge" aria-hidden="true" />;
}

function Money({ value, className = "" }) {
  return (
    <span className={`font-serif tabular-nums ${className}`}>
      ${value ?? "—"}
    </span>
  );
}

export function EmptySlip() {
  return (
    <div className="rounded-md border-2 border-dashed border-line px-6 py-10 text-center">
      <p className="font-serif text-xl font-bold text-fg">Your slip is empty</p>
      <p className="mx-auto mt-2 max-w-[28ch] text-sm leading-6 text-muted">
        Describe what you need and choose a product. Its price, quantity and
        total will appear here before anything is paid.
      </p>
    </div>
  );
}

export default function Slip({
  product,
  productName,
  unitPrice,
  quantity,
  maxQuantity,
  onQuantity,
  budget,
  onBudget,
  estimate,
  plan,
  busy,
  error,
  onReview,
  onApprove,
}) {
  const confirmed = Boolean(plan);
  const total = confirmed ? plan.purchase?.total : estimate;
  const reference = confirmed ? plan.plan_id.slice(0, 8).toUpperCase() : null;

  return (
    <div className="on-paper motion-safe:animate-slip-in">
      <article
        className="rounded-t-md bg-paper px-6 pb-7 pt-6 text-ink"
        aria-label="Purchase slip"
      >
        <header className="flex items-start justify-between gap-4">
          <h2 className="font-serif text-xl font-bold">Purchase slip</h2>
          <p className="pt-1 font-mono text-xs text-ink-muted">
            {reference ? `No. ${reference}` : "Draft"}
          </p>
        </header>

        <div className="mt-5 border-t border-dashed border-paper-line pt-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="font-bold leading-snug">{productName}</p>
              <p className="mt-1 font-mono text-xs text-ink-muted">
                {product.product_id}
              </p>
            </div>
            <p className="shrink-0 text-right text-sm text-ink-muted">
              <Money value={unitPrice} className="text-base text-ink" /> each
            </p>
          </div>

          <div className="mt-5 flex items-center justify-between gap-4">
            <span id="quantity-label" className="text-sm text-ink-muted">
              Quantity (max {maxQuantity})
            </span>
            <div
              className="flex items-center gap-1"
              role="group"
              aria-labelledby="quantity-label"
            >
              <button
                type="button"
                aria-label="Decrease quantity"
                onClick={() => onQuantity(quantity - 1)}
                disabled={busy || quantity <= 1}
                className="flex size-11 items-center justify-center sm:size-10 rounded-md border border-paper-field-line bg-paper-field hover:bg-paper-deep disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Minus size={16} />
              </button>
              <output
                aria-live="polite"
                className="w-10 text-center font-serif text-lg font-bold tabular-nums"
              >
                {quantity}
              </output>
              <button
                type="button"
                aria-label="Increase quantity"
                onClick={() => onQuantity(quantity + 1)}
                disabled={busy || quantity >= maxQuantity}
                className="flex size-11 items-center justify-center sm:size-10 rounded-md border border-paper-field-line bg-paper-field hover:bg-paper-deep disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Plus size={16} />
              </button>
            </div>
          </div>

          <div className="mt-5">
            <label
              htmlFor="budget-limit"
              className="block text-sm text-ink-muted"
            >
              Your maximum budget (USD)
            </label>
            <div className="relative mt-1.5">
              <span
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
              >
                $
              </span>
              <input
                id="budget-limit"
                type="number"
                inputMode="decimal"
                min="0"
                step="any"
                value={budget}
                disabled={busy}
                onChange={(event) => onBudget(event.target.value)}
                className="w-full rounded-md border border-paper-field-line bg-paper-field py-2.5 pl-7 pr-3 font-serif text-lg tabular-nums text-ink placeholder:text-ink-faint disabled:opacity-60"
                placeholder="0.00"
              />
            </div>
          </div>
        </div>

        <div className="mt-6 border-t border-dashed border-paper-line pt-5">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-sm text-ink-muted">
                {confirmed ? "Total confirmed by server" : "Estimated total"}
              </p>
              {confirmed && (
                <p className="mt-1 flex items-center gap-1.5 text-xs font-bold text-ok-ink">
                  <Check size={14} aria-hidden="true" />
                  Price and budget checked
                </p>
              )}
            </div>
            <p className="text-right">
              <Money value={total} className="text-4xl font-bold" />
              <span className="ml-1.5 text-sm text-ink-muted">USD</span>
            </p>
          </div>

          {error && (
            <p
              role="alert"
              className="mt-4 flex items-start gap-2 text-sm text-bad-ink"
            >
              <CircleAlert size={16} className="mt-0.5 shrink-0" />
              {error}
            </p>
          )}

          {confirmed ? (
            <>
              <button
                type="button"
                onClick={onApprove}
                disabled={busy}
                className="mt-5 flex w-full items-center justify-center gap-2 rounded-md bg-indigo px-5 py-3.5 font-bold text-white hover:bg-indigo-deep disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busy ? (
                  <>
                    <LoaderCircle className="animate-spin" size={18} />
                    Connecting to PayPal...
                  </>
                ) : (
                  <>
                    <ShieldCheck size={18} aria-hidden="true" />
                    Approve and pay with PayPal
                  </>
                )}
              </button>
              <p className="mt-3 text-center text-xs leading-5 text-ink-muted">
                Nothing has been charged. PayPal Sandbox opens next and no real
                money moves.
              </p>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={onReview}
                disabled={busy}
                className="mt-5 flex w-full items-center justify-center gap-2 rounded-md bg-indigo px-5 py-3.5 font-bold text-white hover:bg-indigo-deep disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busy ? (
                  <>
                    <LoaderCircle className="animate-spin" size={18} />
                    Creating plan...
                  </>
                ) : (
                  "Review purchase"
                )}
              </button>
              <p className="mt-3 text-center text-xs leading-5 text-ink-muted">
                The server checks the price and your budget before you can
                approve.
              </p>
            </>
          )}
        </div>
      </article>
      <SlipEdge />
    </div>
  );
}
