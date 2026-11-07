import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Headphones,
  LoaderCircle,
  Minus,
  Monitor,
  Package,
  Plus,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Wallet,
} from "lucide-react";

import { apiRequest, isPayPalUrl, planAccessHeaders, storePlanAccessToken } from "./api";
import PaymentResult from "./PaymentResult.jsx";

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

      storePlanAccessToken(data.plan_id, data.plan_access_token);
      const { plan_access_token: _accessToken, ...safePlan } = data;
      setPlan(safePlan);
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
          headers: planAccessHeaders(plan.plan_id),
          body: JSON.stringify({ approved: true }),
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

  function productIcon(product) {
    const category = (product.category || "").toLowerCase();
    if (category === "audio") return Headphones;
    if (category === "workspace") return Monitor;
    return Package;
  }
  return (
    <main className="min-h-screen bg-[#09090f] text-slate-100">
      <nav className="mx-auto flex max-w-7xl items-center justify-between px-6 py-6">
        <a href="/" className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-600">
            <ShoppingBag size={21} />
          </div>
          <span className="text-xl font-bold tracking-tight">
            NEXT<span className="text-violet-400">PAY</span>
          </span>
        </a>

        <div className="hidden items-center gap-7 text-sm text-slate-400 sm:flex">
          <a href="#how-it-works" className="transition hover:text-white">
            How it works
          </a>
          <a href="#shopping" className="transition hover:text-white">
            AI Shopping
          </a>
          <span className="flex items-center gap-2 text-emerald-400">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            Sandbox environment
          </span>
        </div>
      </nav>

      <section className="mx-auto max-w-7xl px-6 pb-20 pt-16">
        <div className="mx-auto max-w-4xl text-center">
          <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-violet-500/30 bg-violet-500/10 px-4 py-2 text-sm text-violet-300">
            <Sparkles size={15} />
            AI-powered agentic commerce
          </div>

          <h1 className="text-4xl font-bold leading-tight tracking-tight sm:text-6xl">
            Shopping that
            <span className="block text-violet-400">
              thinks before you pay.
            </span>
          </h1>

          <p className="mx-auto mt-6 max-w-2xl text-base leading-7 text-slate-400 sm:text-lg">
            Tell NEXTPAY what you need. Our AI understands your intent, finds
            suitable products and checks your budget before you approve
            anything.
          </p>
        </div>

        <div id="shopping" className="mx-auto mt-14 max-w-3xl">
          <div className="rounded-3xl border border-white/10 bg-[#11111b] p-5 shadow-2xl shadow-violet-950/20 sm:p-8">
            <div className="mb-6 flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-violet-500/15 text-violet-300">
                <Sparkles size={21} />
              </div>
              <div>
                <h2 className="font-semibold">What are you looking for?</h2>
                <p className="text-sm text-slate-500">
                  Describe your shopping goal naturally.
                </p>
              </div>
            </div>

            <form onSubmit={getRecommendations}>
              <label
                htmlFor="shopping-message"
                className="mb-2 block text-sm font-medium text-slate-300"
              >
                Your shopping request
              </label>

              <textarea
                id="shopping-message"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                placeholder="I need comfortable headphones for studying, under $70. Prioritise battery life and sound quality..."
                rows={5}
                maxLength={1000}
                className="w-full resize-none rounded-2xl border border-white/10 bg-[#09090f] p-4 text-sm leading-7 text-white outline-none transition placeholder:text-slate-600 focus:border-violet-500"
              />

              <div className="mt-2 flex justify-between text-xs text-slate-500">
                <span>Be specific about your needs and budget.</span>
                <span>{message.length}/1000</span>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-violet-600 px-5 py-4 font-semibold text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loading ? (
                  <>
                    <LoaderCircle className="animate-spin" size={19} />
                    Analysing your request...
                  </>
                ) : (
                  <>
                    <Sparkles size={18} />
                    Find my products
                    <ArrowRight size={18} />
                  </>
                )}
              </button>
            </form>

            {error && (
              <div
                role="alert"
                className="mt-5 flex items-start gap-3 rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300"
              >
                <AlertCircle size={19} className="mt-0.5 shrink-0" />
                {error}
              </div>
            )}
          </div>

          {result && (
            <section className="mt-8 space-y-5">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="text-emerald-400" size={20} />
                <h2 className="text-xl font-semibold">AI analysis complete</h2>
              </div>

              {result.message && (
                <p className="text-sm text-slate-400">{result.message}</p>
              )}

              {result.intent && (
                <div className="rounded-2xl border border-white/10 bg-[#11111b] p-5">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-violet-300">
                    Understood shopping intent
                  </p>
                  <p className="text-sm leading-7 text-slate-300">
                    {result.intent.summary ||
                      "Your shopping preferences have been analysed."}
                  </p>
                  {result.intent.budget_limit && (
                    <p className="mt-3 flex items-center gap-2 text-sm text-slate-400">
                      <Wallet size={16} />
                      Budget: ${result.intent.budget_limit}
                    </p>
                  )}
                </div>
              )}

              {needsClarification && (
                <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 p-5 text-sm text-amber-200">
                  {result.question ||
                    result.clarification_question ||
                    "Please provide more information about your budget or preferences."}
                </div>
              )}

              {!needsClarification && recommendations.length > 0 ? (
                <div>
                  <h3 className="mb-4 text-lg font-semibold">
                    Recommended products
                  </h3>
                  <div className="grid gap-4 sm:grid-cols-2">
                    {recommendations.map((product, index) => {
                      const Icon = productIcon(product);
                      const isSelected =
                        selectedProduct?.product_id === product.product_id;

                      return (
                        <div
                          key={product.product_id || index}
                          className="space-y-4"
                        >
                          <article
                            role="button"
                            tabIndex={0}
                            aria-pressed={isSelected}
                            onClick={() => chooseProduct(product)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                chooseProduct(product);
                              }
                            }}
                            className={`cursor-pointer rounded-2xl border p-5 transition ${
                              isSelected
                                ? "border-violet-500 bg-violet-500/10"
                                : "border-white/10 bg-[#11111b] hover:border-violet-500/50"
                            }`}
                          >
                            <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-xl bg-violet-500/10 text-violet-300">
                              <Icon size={23} />
                            </div>
                            <h4 className="font-semibold">
                              {productName(product)}
                            </h4>
                            <p className="mt-2 text-xl font-bold text-violet-300">
                              ${productPrice(product)}
                            </p>
                            <p className="mt-3 text-xs text-slate-500">
                              Sample catalogue product · Not yet purchased
                            </p>
                            <p className="mt-4 text-sm font-medium text-violet-300">
                              {isSelected
                                ? "✓ Selected"
                                : "Select this product →"}
                            </p>
                          </article>

                          {isSelected && (
                            <section className="rounded-2xl border border-violet-500/30 bg-[#11111b] p-6">
                              <h3 className="text-lg font-semibold">
                                Review your purchase
                              </h3>

                              <div className="mt-5 flex justify-between gap-4">
                                <div>
                                  <p className="font-medium">
                                    {productName(selectedProduct)}
                                  </p>
                                  <p className="mt-1 text-sm text-slate-500">
                                    ${productPrice(selectedProduct)} each
                                  </p>
                                </div>

                                <p className="font-bold text-violet-300">
                                  $
                                  {estimateTotal(
                                    productPrice(selectedProduct),
                                    quantity,
                                  ) ?? "—"}
                                </p>
                              </div>

                              <div className="mt-5 flex items-center justify-between">
                                <span className="text-sm text-slate-300">
                                  Quantity (max {MAX_QUANTITY})
                                </span>
                                <div className="flex items-center gap-3">
                                  <button
                                    type="button"
                                    aria-label="Decrease quantity"
                                    onClick={() => changeQuantity(quantity - 1)}
                                    disabled={purchaseLoading || quantity <= 1}
                                    className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/10 hover:border-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
                                  >
                                    <Minus size={16} />
                                  </button>
                                  <span
                                    className="w-6 text-center font-semibold"
                                    aria-live="polite"
                                  >
                                    {quantity}
                                  </span>
                                  <button
                                    type="button"
                                    aria-label="Increase quantity"
                                    onClick={() => changeQuantity(quantity + 1)}
                                    disabled={
                                      purchaseLoading || quantity >= MAX_QUANTITY
                                    }
                                    className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/10 hover:border-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
                                  >
                                    <Plus size={16} />
                                  </button>
                                </div>
                              </div>

                              <label className="mt-6 block text-sm text-slate-300">
                                Your maximum budget (USD)
                              </label>

                              <input
                                type="number"
                                min="0.01"
                                step="0.01"
                                value={budgetLimit}
                                disabled={purchaseLoading}
                                onChange={(event) => {
                                  setBudgetLimit(event.target.value);
                                  setPlan(null);
                                }}
                                className="mt-2 w-full rounded-xl border border-white/10 bg-[#09090f] p-3 text-white outline-none focus:border-violet-500"
                              />

                              {purchaseError && (
                                <p role="alert" className="mt-4 text-sm text-red-400">
                                  {purchaseError}
                                </p>
                              )}

                              {!plan ? (
                                <button
                                  onClick={createPurchasePlan}
                                  disabled={purchaseLoading}
                                  className="mt-5 w-full rounded-xl bg-violet-600 p-4 font-semibold hover:bg-violet-500 disabled:opacity-50"
                                >
                                  {purchaseLoading ? (
                                    <span className="flex items-center justify-center gap-2">
                                      <LoaderCircle
                                        className="animate-spin"
                                        size={18}
                                      />
                                      Creating plan...
                                    </span>
                                  ) : (
                                    "Review purchase"
                                  )}
                                </button>
                              ) : (
                                <div className="mt-5 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                                  <p className="font-medium text-emerald-300">
                                    Purchase plan created
                                  </p>

                                  <p className="mt-2 text-sm text-slate-400">
                                    {plan.purchase?.quantity ?? quantity} ×{" "}
                                    {plan.purchase?.product_name}
                                  </p>
                                  <p className="mt-1 text-sm text-slate-400">
                                    Server-validated total: $
                                    {plan.purchase?.total ?? "See plan details"}{" "}
                                    USD
                                  </p>

                                  <p className="mt-2 text-xs text-slate-500">
                                    Plan ID: {plan.plan_id}
                                  </p>
                                  <p className="mt-3 text-sm text-slate-300">
                                    No purchase has been approved yet.
                                  </p>
                                  <p className="mt-1 text-xs text-slate-500">
                                    PayPal Sandbox checkout: no real money
                                    moves.
                                  </p>

                                  <button
                                    onClick={approveAndCheckout}
                                    disabled={purchaseLoading}
                                    className="mt-4 w-full rounded-xl bg-emerald-600 p-4 font-semibold hover:bg-emerald-500 disabled:opacity-50"
                                  >
                                    {purchaseLoading ? (
                                      <span className="flex items-center justify-center gap-2">
                                        <LoaderCircle
                                          className="animate-spin"
                                          size={18}
                                        />
                                        Connecting to PayPal...
                                      </span>
                                    ) : (
                                      "Approve purchase & continue to PayPal"
                                    )}
                                  </button>
                                </div>
                              )}
                            </section>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : (
                !needsClarification && (
                  <div className="rounded-2xl border border-white/10 bg-[#11111b] p-5 text-sm text-slate-400">
                    No matching products were found. Try adjusting your budget
                    or shopping preferences.
                  </div>
                )
              )}

              <div className="flex items-center gap-2 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4 text-xs text-emerald-300">
                <ShieldCheck size={17} />
                No purchase has been made. Your explicit approval is required.
              </div>
            </section>
          )}
        </div>

        <section id="how-it-works" className="mx-auto mt-24 max-w-5xl">
          <h2 className="text-center text-2xl font-bold">
            Commerce with control
          </h2>
          <div className="mt-9 grid gap-4 sm:grid-cols-3">
            {[
              {
                icon: Sparkles,
                title: "Understand",
                description:
                  "AI converts your natural-language request into structured shopping intent.",
              },
              {
                icon: Wallet,
                title: "Validate",
                description:
                  "Server-side rules check product prices, quantity and your budget.",
              },
              {
                icon: ShieldCheck,
                title: "Approve",
                description:
                  "You stay in control. Payment requires your explicit approval.",
              },
            ].map((item) => (
              <div
                key={item.title}
                className="rounded-2xl border border-white/10 bg-[#11111b] p-6"
              >
                <item.icon className="mb-4 text-violet-400" size={24} />
                <h3 className="font-semibold">{item.title}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-500">
                  {item.description}
                </p>
              </div>
            ))}
          </div>
        </section>

        <footer className="mt-24 border-t border-white/10 pt-7 text-center text-xs text-slate-600">
          NEXTPAY · AI-assisted commerce · Sandbox demonstration
        </footer>
      </section>
    </main>
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
