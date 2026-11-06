import { useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Headphones,
  LoaderCircle,
  Monitor,
  Package,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Wallet,
} from "lucide-react";

const API_URL = "http://127.0.0.1:8000";

function App() {
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [budgetLimit, setBudgetLimit] = useState("");
  const [plan, setPlan] = useState(null);
  const [purchaseLoading, setPurchaseLoading] = useState(false);
  const [purchaseError, setPurchaseError] = useState("");
  const paymentPath = window.location.pathname;
  const paymentParams = new URLSearchParams(window.location.search);
  const paymentPlanId = paymentParams.get("plan_id");

  const [paymentPlan, setPaymentPlan] = useState(null);
  const [paymentLoading, setPaymentLoading] = useState(
    paymentPath === "/payment/success",
  );
  const [paymentError, setPaymentError] = useState("");

  useEffect(() => {
    if (paymentPath !== "/payment/success" || !paymentPlanId) {
      setPaymentLoading(false);
      return;
    }

    let active = true;

    async function loadReceipt() {
      try {
        const response = await fetch(
          `${API_URL}/api/plans/${encodeURIComponent(paymentPlanId)}`,
        );

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || "Could not load payment receipt.");
        }

        const planData = data.plan || data;

        if (planData.payment_status !== "completed") {
          throw new Error("Payment completion could not be verified.");
        }

        if (active) setPaymentPlan(planData);
      } catch (err) {
        if (active) setPaymentError(err.message);
      } finally {
        if (active) setPaymentLoading(false);
      }
    }

    loadReceipt();

    return () => {
      active = false;
    };
  }, [paymentPath, paymentPlanId]);

  async function createPurchasePlan() {
    if (!selectedProduct) {
      setPurchaseError("Select a product first.");
      return;
    }

    if (!selectedProduct.product_id) {
      setPurchaseError("The recommendation has no valid product ID.");
      return;
    }

    if (!budgetLimit || Number(budgetLimit) <= 0) {
      setPurchaseError("Enter a valid purchase budget.");
      return;
    }

    setPurchaseLoading(true);
    setPurchaseError("");

    try {
      const response = await fetch(`${API_URL}/api/plans`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          product_id: selectedProduct.product_id,
          quantity: 1,
          budget_limit: Number(budgetLimit),
          goal: message || "Shop for a recommended product.",
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Could not create purchase plan.");
      }

      setPlan(data);
    } catch (err) {
      setPurchaseError(err.message);
    } finally {
      setPurchaseLoading(false);
    }
  }

  async function getRecommendations(event) {
    event.preventDefault();

    if (message.trim().length < 10) {
      setError("Please describe what you want in at least 10 characters.");
      return;
    }

    setLoading(true);
    setError("");
    setSelectedProduct(null);
    setPlan(null);
    setPurchaseError("");
    setResult(null);

    try {
      const response = await fetch(`${API_URL}/api/agent/recommend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Unable to get recommendations.");
      }

      setResult(data);
      setBudgetLimit(
        data.intent?.budget_limit ? String(data.intent.budget_limit) : "",
      );
    } catch (err) {
      setError(
        err.message || "Could not connect to NEXTPAY. Check your backend.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function approveAndCheckout() {
    if (!plan?.plan_id) return;

    setPurchaseLoading(true);
    setPurchaseError("");

    try {
      const approvalResponse = await fetch(
        `${API_URL}/api/plans/${plan.plan_id}/approval`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved: true }),
        },
      );

      const approvalData = await approvalResponse.json();

      if (!approvalResponse.ok) {
        throw new Error(approvalData.detail || "Purchase approval failed.");
      }

      const orderResponse = await fetch(
        `${API_URL}/api/plans/${plan.plan_id}/paypal/create-order`,
        { method: "POST" },
      );

      const orderData = await orderResponse.json();

      if (!orderResponse.ok) {
        throw new Error(orderData.detail || "Could not create PayPal order.");
      }

      if (!orderData.approval_url) {
        throw new Error("PayPal did not return a checkout URL.");
      }

      window.location.assign(orderData.approval_url);
    } catch (err) {
      setPurchaseError(err.message);
    } finally {
      setPurchaseLoading(false);
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
  if (paymentPath === "/payment/success") {
    if (paymentLoading) {
      return (
        <main className="flex min-h-screen items-center justify-center bg-[#09090f] text-white">
          <p className="text-slate-400">Verifying your payment receipt...</p>
        </main>
      );
    }

    if (paymentError || !paymentPlan) {
      return (
        <main className="flex min-h-screen items-center justify-center bg-[#09090f] px-5 text-white">
          <div className="max-w-md rounded-3xl border border-amber-500/20 bg-[#11111b] p-8 text-center">
            <h1 className="text-2xl font-bold text-amber-300">
              Receipt unavailable
            </h1>
            <p className="mt-4 text-sm text-slate-400">
              {paymentError || "We could not verify this transaction."}
            </p>
            <a
              href="/"
              className="mt-6 inline-block rounded-xl bg-violet-600 px-6 py-3 font-semibold"
            >
              Return to NEXTPAY
            </a>
          </div>
        </main>
      );
    }

    const purchase = paymentPlan.purchase || {};

    return (
      <main className="flex min-h-screen items-center justify-center bg-[#09090f] px-5 py-12 text-white">
        <div className="w-full max-w-lg rounded-3xl border border-emerald-500/20 bg-[#11111b] p-8">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-400">
            <CheckCircle2 size={34} />
          </div>

          <h1 className="mt-6 text-center text-3xl font-bold">
            Payment successful
          </h1>

          <p className="mt-3 text-center text-slate-400">
            Your PayPal Sandbox payment was captured and verified.
          </p>

          <div className="mt-8 space-y-4 rounded-2xl bg-[#09090f] p-5">
            <div className="flex justify-between gap-4">
              <span className="text-slate-500">Product</span>
              <span className="text-right">
                {purchase.product_name || "NEXTPAY purchase"}
              </span>
            </div>

            <div className="flex justify-between">
              <span className="text-slate-500">Quantity</span>
              <span>{purchase.quantity ?? "—"}</span>
            </div>

            <div className="flex justify-between">
              <span className="text-slate-500">Amount</span>
              <span className="font-bold text-emerald-300">
                ${purchase.total ?? "—"} USD
              </span>
            </div>

            <div className="border-t border-white/10 pt-4">
              <p className="text-xs text-slate-500">PayPal capture ID</p>
              <p className="mt-1 break-all text-xs">
                {paymentPlan.paypal_capture_id || "Unavailable"}
              </p>
            </div>

            <div className="flex justify-between">
              <span className="text-slate-500">Payment status</span>
              <span className="text-emerald-400">COMPLETED</span>
            </div>
          </div>

          <a
            href="/"
            className="mt-7 block rounded-xl bg-violet-600 px-5 py-4 text-center font-semibold hover:bg-violet-500"
          >
            Continue shopping
          </a>
        </div>
      </main>
    );
  }

  if (
    paymentPath === "/payment/failure" ||
    paymentPath === "/payment/cancelled"
  ) {
    const cancelled = paymentPath === "/payment/cancelled";
    const reason = paymentParams.get("reason");

    return (
      <main className="flex min-h-screen items-center justify-center bg-[#09090f] px-5 text-white">
        <div className="w-full max-w-md rounded-3xl border border-white/10 bg-[#11111b] p-8 text-center">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-amber-500/10 text-amber-300">
            {cancelled ? <AlertCircle size={32} /> : <AlertCircle size={32} />}
          </div>

          <h1 className="mt-6 text-2xl font-bold">
            {cancelled ? "Checkout cancelled" : "Payment not verified"}
          </h1>

          <p className="mt-3 text-sm leading-6 text-slate-400">
            {cancelled
              ? "You cancelled the PayPal checkout. No successful payment has been recorded by NEXTPAY."
              : "NEXTPAY could not verify a completed payment. Please check your transaction status before trying again."}
          </p>

          {reason && (
            <p className="mt-4 break-all text-xs text-slate-600">
              Reference: {reason}
            </p>
          )}

          <a
            href="/"
            className="mt-7 block rounded-xl bg-violet-600 px-5 py-4 font-semibold hover:bg-violet-500"
          >
            Return to NEXTPAY
          </a>
        </div>
      </main>
    );
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
            <span className="block bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
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
              <div className="mt-5 flex items-start gap-3 rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
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
                            onClick={() => {
                              setSelectedProduct(product);
                              setPlan(null);
                              setPurchaseError("");
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
                                    Quantity: 1
                                  </p>
                                </div>

                                <p className="font-bold text-violet-300">
                                  ${productPrice(selectedProduct)}
                                </p>
                              </div>

                              <label className="mt-6 block text-sm text-slate-300">
                                Your maximum budget (USD)
                              </label>

                              <input
                                type="number"
                                min="0.01"
                                step="0.01"
                                value={budgetLimit}
                                onChange={(event) =>
                                  setBudgetLimit(event.target.value)
                                }
                                className="mt-2 w-full rounded-xl border border-white/10 bg-[#09090f] p-3 text-white outline-none focus:border-violet-500"
                              />

                              {purchaseError && (
                                <p className="mt-4 text-sm text-red-400">
                                  {purchaseError}
                                </p>
                              )}

                              {!plan ? (
                                <button
                                  onClick={createPurchasePlan}
                                  disabled={purchaseLoading}
                                  className="mt-5 w-full rounded-xl bg-violet-600 p-4 font-semibold hover:bg-violet-500 disabled:opacity-50"
                                >
                                  {purchaseLoading
                                    ? "Creating plan..."
                                    : "Review purchase"}
                                </button>
                              ) : (
                                <div className="mt-5 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                                  <p className="font-medium text-emerald-300">
                                    Purchase plan created
                                  </p>

                                  <p className="mt-2 text-sm text-slate-400">
                                    Server-validated total: $
                                    {plan.purchase?.total ?? "See plan details"}
                                  </p>

                                  <p className="mt-2 text-xs text-slate-500">
                                    Plan ID: {plan.plan_id}
                                  </p>
                                  <p className="mt-3 text-sm text-slate-300">
                                    No purchase has been approved yet.
                                  </p>

                                  <button
                                    onClick={approveAndCheckout}
                                    disabled={purchaseLoading}
                                    className="mt-4 w-full rounded-xl bg-emerald-600 p-4 font-semibold hover:bg-emerald-500 disabled:opacity-50"
                                  >
                                    {purchaseLoading
                                      ? "Connecting to PayPal..."
                                      : "Approve purchase & continue to PayPal"}
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

export default App;
