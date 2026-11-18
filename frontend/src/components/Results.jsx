import { Check, CircleAlert } from "lucide-react";

// AI results: what was understood, an optional clarifying question, and the
// shortlist of products the buyer can put on the slip.

function list(value) {
  return Array.isArray(value) ? value.filter(Boolean) : [];
}

export function Understood({ intent, message }) {
  if (!intent && !message) return null;

  const preferences = list(intent?.preferences);
  const avoid = list(intent?.must_avoid);

  return (
    <section aria-labelledby="understood-title">
      <h2
        id="understood-title"
        className="font-serif text-2xl font-bold text-fg"
      >
        What NEXTPAY understood
      </h2>

      {message && <p className="mt-2 text-sm text-muted">{message}</p>}

      {intent && (
        <>
          <p className="mt-3 max-w-[62ch] text-pretty leading-7 text-fg">
            {intent.summary || "Your shopping preferences have been analysed."}
          </p>

          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
            {intent.budget_limit && (
              <>
                <dt className="text-muted">Budget</dt>
                <dd className="font-serif tabular-nums text-fg">
                  ${intent.budget_limit}
                </dd>
              </>
            )}
            {preferences.length > 0 && (
              <>
                <dt className="text-muted">Looking for</dt>
                <dd className="text-fg">{preferences.join(", ")}</dd>
              </>
            )}
            {avoid.length > 0 && (
              <>
                <dt className="text-muted">Avoiding</dt>
                <dd className="text-fg">{avoid.join(", ")}</dd>
              </>
            )}
          </dl>
        </>
      )}
    </section>
  );
}

export function Clarification({ question }) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-warn/40 bg-panel p-4">
      <CircleAlert size={18} className="mt-0.5 shrink-0 text-warn" />
      <div>
        <p className="font-bold text-fg">One more detail needed</p>
        <p className="mt-1 text-sm leading-6 text-muted">
          {question ||
            "Please add more detail about what you want and your budget, then search again."}
        </p>
      </div>
    </div>
  );
}

export function NoMatches() {
  return (
    <div className="rounded-md border border-dashed border-line px-5 py-6">
      <p className="font-bold text-fg">No matching products</p>
      <p className="mt-1 text-sm leading-6 text-muted">
        Try adjusting your budget or describing a different item.
      </p>
    </div>
  );
}

export function ProductList({ products, selectedId, onSelect, name, price }) {
  return (
    <section aria-labelledby="products-title">
      <div className="flex items-baseline justify-between gap-4">
        <h2
          id="products-title"
          className="font-serif text-2xl font-bold text-fg"
        >
          Products that fit
        </h2>
        <p className="text-sm text-muted">
          {products.length} {products.length === 1 ? "match" : "matches"}
        </p>
      </div>

      <ul className="mt-4 divide-y divide-line border-y border-line">
        {products.map((product, index) => {
          const selected = selectedId === product.product_id;

          return (
            <li key={product.product_id || index}>
              <button
                type="button"
                aria-pressed={selected}
                onClick={() => onSelect(product)}
                className={`relative flex w-full items-center gap-4 px-4 py-4 text-left transition-colors sm:px-5 ${
                  selected ? "bg-panel" : "hover:bg-panel/60"
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`absolute inset-y-0 left-0 w-[3px] ${
                    selected ? "bg-accent" : "bg-transparent"
                  }`}
                />
                <span
                  aria-hidden="true"
                  className={`flex size-5 shrink-0 items-center justify-center rounded-full border-2 ${
                    selected
                      ? "border-accent bg-accent text-canvas"
                      : "border-line-strong"
                  }`}
                >
                  {selected && <Check size={12} strokeWidth={3} />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block font-bold text-fg">
                    {name(product)}
                  </span>
                  <span className="mt-0.5 block text-sm text-muted">
                    Sample catalogue item
                  </span>
                </span>
                <span className="font-serif text-xl tabular-nums text-fg">
                  ${price(product)}
                </span>
                <span
                  className={`hidden w-24 text-right text-sm sm:block ${
                    selected ? "text-accent" : "text-muted"
                  }`}
                >
                  {selected ? "On your slip" : "Add to slip"}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
