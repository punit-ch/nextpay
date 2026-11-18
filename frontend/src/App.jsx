import { useEffect, useRef, useState } from "react";
import { ArrowRight, CircleAlert, LoaderCircle } from "lucide-react";

import {
  apiRequest,
  isPayPalUrl,
  planAccessHeaders,
  storePlanAccessToken,
} from "./api";
import { Footer, StepTracker, TopBar } from "./components/chrome.jsx";
import {
  Clarification,
  NoMatches,
  ProductList,
  Understood,
} from "./components/Results.jsx";
import Slip, { EmptySlip } from "./components/Slip.jsx";
import PaymentResult from "./PaymentResult.jsx";

const EXAMPLES = [
  "Comfortable headphones for studying, under $70",
  "A desk lamp for working at night",
  "A compact keyboard for travel, under $50",
];

const MAX_QUANTITY = 5;

// Display-only estimate in whole cents. The server calculates the real total.
function estimateTotal(price, quantity) {
  const cents = Math.round(Number.parseFloat(price) * 100);
  if (!Number.isFinite(cents)) return null;
  return ((cents * quantity) / 100).toFixed(2);
}

function ShoppingFlow() {
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [budgetLimit, setBudgetLimit] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [plan, setPlan] = useState(null);
  const [purchaseLoading, setPurchaseLoading] = useState(false);
  const [purchaseError, setPurchaseError] = useState("");

  // A ref blocks double clicks instantly; state alone updates one render late.
  const busy = useRef(false);
  const slipRef = useRef(null);
  const messageRef = useRef(null);

  useEffect(() => {
    // Returning with the browser back button restores this page from cache
    // with the "Connecting to PayPal" state still on screen.
    function reset(event) {
      if (event.persisted) {
        busy.current = false;
        setPurchaseLoading(false);
      }
    }
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  useEffect(() => {
    // On small screens the slip sits below the list: show it when a product is chosen.
    if (!selectedProduct || !window.matchMedia("(max-width: 1023px)").matches) {
      return;
    }
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    slipRef.current?.scrollIntoView({
      behavior: reduce ? "auto" : "smooth",
      block: "nearest",
    });
  }, [selectedProduct]);

  function fillExample(example) {
    setMessage(example);
    messageRef.current?.focus();
  }

  function chooseProduct(product) {
    setSelectedProduct(product);
    setQuantity(1);
    setPlan(null);
    setPurchaseError("");
  }

  function changeQuantity(next) {
    const clamped = Math.min(MAX_QUANTITY, Math.max(1, next));
    setQuantity(clamped);
    // A plan is tied to one quantity; changing it requires a new review.
    setPlan(null);
    setPurchaseError("");
  }

  async function createPurchasePlan() {
    if (busy.current) return;

    if (!selectedProduct?.product_id) {
      setPurchaseError("Select a product first.");
      return;
    }

    if (!budgetLimit || Number(budgetLimit) <= 0) {
      setPurchaseError("Enter a valid purchase budget.");
      return;
    }

    busy.current = true;
    setPurchaseLoading(true);
    setPurchaseError("");

    try {
      const data = await apiRequest("/api/plans", {
        method: "POST",
        body: JSON.stringify({
          product_id: selectedProduct.product_id,
          quantity,
          budget_limit: Number(budgetLimit),
          goal: message || "Shop for a recommended product.",
        }),
      });

      // The access token is issued once. Keep it in storage (it must survive
      // the trip to PayPal and back), not in React state.
      if (!storePlanAccessToken(data.plan_id, data.access_token)) {
        throw new Error("Could not secure this purchase plan. Please try again.");
      }

      const publicPlan = { ...data };
      delete publicPlan.access_token;
      setPlan(publicPlan);
    } catch (err) {
      setPurchaseError(err.message);
    } finally {
      busy.current = false;
      setPurchaseLoading(false);
    }
  }

  async function getRecommendations(event) {
    event.preventDefault();
    if (busy.current || loading) return;

    if (message.trim().length < 10) {
      setError("Please describe what you want in at least 10 characters.");
      return;
    }

    busy.current = true;
    setLoading(true);
    setError("");
    setSelectedProduct(null);
    setPlan(null);
    setPurchaseError("");
    setResult(null);

    try {
      const data = await apiRequest("/api/agent/recommend", {
        method: "POST",
        body: JSON.stringify({ message }),
      });

      setResult(data);
      setBudgetLimit(
        data.intent?.budget_limit ? String(data.intent.budget_limit) : "",
      );
    } catch (err) {
      setError(err.message);
    } finally {
      busy.current = false;
      setLoading(false);
    }
  }

  async function approveAndCheckout() {
    if (busy.current || !plan?.plan_id) return;

    busy.current = true;
    setPurchaseLoading(true);
    setPurchaseError("");

    let redirecting = false;

    try {
      let current = plan;

      // Approval is recorded once. If PayPal order creation failed earlier,
      // a retry must not approve again (the server would answer 409).
      if (current.status === "awaiting_user_approval") {
        current = await apiRequest(`/api/plans/${plan.plan_id}/approval`, {
          method: "POST",
          body: JSON.stringify({ approved: true }),
          headers: planAccessHeaders(plan.plan_id),
        });
        setPlan(current);
      }

      if (current.status !== "approved_by_user") {
        throw new Error(
          "This purchase plan can no longer be approved. Please review it again.",
        );
      }

      const order = await apiRequest(
        `/api/plans/${plan.plan_id}/paypal/create-order`,
        { method: "POST", headers: planAccessHeaders(plan.plan_id) },
      );

      if (!isPayPalUrl(order.approval_url)) {
        throw new Error("PayPal did not return a valid checkout URL.");
      }

      // Keep the button disabled while the browser leaves for PayPal.
      redirecting = true;
      window.location.assign(order.approval_url);
    } catch (err) {
      setPurchaseError(err.message);
    } finally {
      if (!redirecting) {
        busy.current = false;
        setPurchaseLoading(false);
      }
    }
  }

  const recommendations = result?.recommendations || [];
  const needsClarification =
    result?.status === "needs_clarification" || result?.needs_clarification;

  function productName(product) {
    return (
      product.product_name ||
      product.name ||
      product.title ||
      "Recommended product"
    );
  }

  function productPrice(product) {
    return (
      product.price ?? product.unit_price ?? product.displayed_price ?? "—"
    );
  }

  const step = plan ? 3 : result && !needsClarification ? 2 : 1;

  return (
    <div className="min-h-screen bg-canvas text-fg">
      <a
        href="#main"
        className="sr-only rounded-md bg-accent px-4 py-2 font-bold text-canvas focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-10"
      >
        Skip to content
      </a>

      <TopBar />

      <main
        id="main"
        className="mx-auto grid max-w-[1180px] gap-x-16 gap-y-14 px-5 pb-24 pt-10 sm:px-8 lg:grid-cols-[minmax(0,1fr)_400px] lg:pt-16"
      >
        <div className="min-w-0 space-y-14">
          <section>
            <StepTracker step={step} />

            <h1 className="mt-10 max-w-[18ch] text-balance font-serif text-4xl font-bold leading-[1.1] sm:text-5xl">
              Ask for what you need. Approve what gets paid.
            </h1>
            <p className="mt-5 max-w-[56ch] text-pretty text-lg leading-8 text-muted">
              NEXTPAY turns a plain-language request into a short list from its
              catalogue, checks the price and your budget on the server, and
              only then opens PayPal.
            </p>

            <form onSubmit={getRecommendations} className="mt-9" noValidate>
              <label htmlFor="shopping-message" className="block font-bold">
                What are you looking for?
              </label>
              <textarea
                id="shopping-message"
                ref={messageRef}
                rows={4}
                maxLength={1000}
                value={message}
                disabled={loading}
                onChange={(event) => setMessage(event.target.value)}
                aria-describedby="message-hint"
                placeholder="e.g. Comfortable headphones for studying, under $70"
                className="mt-2 w-full resize-y rounded-md border border-line-strong bg-panel px-4 py-3 text-lg leading-7 text-fg placeholder:text-faint disabled:opacity-60"
              />
              <div
                id="message-hint"
                className="mt-2 flex items-center justify-between gap-4 text-sm text-muted"
              >
                <span>Be specific about the item and your budget.</span>
                <span className="tabular-nums">{message.length}/1000</span>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <span className="text-sm text-muted">Try:</span>
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => fillExample(example)}
                    disabled={loading}
                    className="rounded-full border border-line-strong px-4 py-2 text-left text-sm text-muted hover:border-accent hover:text-fg disabled:opacity-60"
                  >
                    {example}
                  </button>
                ))}
              </div>

              {error && (
                <p
                  role="alert"
                  className="mt-5 flex items-start gap-2 rounded-md border border-bad/40 bg-panel p-4 text-sm leading-6 text-bad"
                >
                  <CircleAlert size={18} className="mt-0.5 shrink-0" />
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={loading}
                className="mt-6 flex w-full items-center justify-center gap-2 rounded-md bg-accent px-6 py-3.5 font-bold text-canvas hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loading ? (
                  <>
                    <LoaderCircle className="animate-spin" size={18} />
                    Reading your request...
                  </>
                ) : (
                  <>
                    Find products
                    <ArrowRight size={18} aria-hidden="true" />
                  </>
                )}
              </button>
            </form>
          </section>

          <p className="sr-only" role="status">
            {loading ? "Reading your request." : ""}
            {!loading && result && !needsClarification
              ? `${recommendations.length} matching products.`
              : ""}
          </p>

          {result && (
            <div className="space-y-12">
              <Understood intent={result.intent} message={result.message} />

              {needsClarification && (
                <Clarification
                  question={result.question || result.clarification_question}
                />
              )}

              {!needsClarification && recommendations.length > 0 && (
                <ProductList
                  products={recommendations}
                  selectedId={selectedProduct?.product_id}
                  onSelect={chooseProduct}
                  name={productName}
                  price={productPrice}
                />
              )}

              {!needsClarification && recommendations.length === 0 && (
                <NoMatches />
              )}
            </div>
          )}
        </div>

        <aside
          ref={slipRef}
          aria-label="Your purchase slip"
          className="scroll-mt-6 lg:sticky lg:top-8 lg:self-start"
        >
          {selectedProduct ? (
            <Slip
              product={selectedProduct}
              productName={productName(selectedProduct)}
              unitPrice={productPrice(selectedProduct)}
              quantity={quantity}
              maxQuantity={MAX_QUANTITY}
              onQuantity={changeQuantity}
              budget={budgetLimit}
              onBudget={(value) => {
                setBudgetLimit(value);
                setPlan(null);
              }}
              estimate={estimateTotal(productPrice(selectedProduct), quantity)}
              plan={plan}
              busy={purchaseLoading}
              error={purchaseError}
              onReview={createPurchasePlan}
              onApprove={approveAndCheckout}
            />
          ) : (
            <EmptySlip />
          )}
        </aside>
      </main>

      <Footer />
    </div>
  );
}

function App() {
  const path = window.location.pathname;

  if (path.startsWith("/payment/")) {
    return <PaymentResult path={path} />;
  }

  return <ShoppingFlow />;
}

export default App;
