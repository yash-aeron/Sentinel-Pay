import { useCallback, useEffect, useState } from "react";
import { approve, listTransactions, reject } from "./api";
import { decisionClass, money, type FeedEvent, type Transaction } from "./types";
import { useEventStream } from "./useEventStream";

const TOKEN_STORAGE_KEY = "sentinelpay.analyst-token";

function eventSummary(event: FeedEvent): string {
  if (event.type === "transaction.created" && event.transaction) {
    const tx = event.transaction;
    return `${tx.status.toUpperCase()}  ${money(tx.amount_minor, tx.currency)}${
      tx.decision === "REVIEW" || tx.decision === "DECLINE"
        ? `  (${tx.decision}, score ${tx.risk_score?.toFixed(2) ?? "?"})`
        : ""
    }`;
  }
  if (event.type === "review.decided") {
    return `${event.action?.toUpperCase()} -> ${event.status}  by ${event.analyst ?? "?"}`;
  }
  return event.type;
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_STORAGE_KEY) ?? "");
  const [draftToken, setDraftToken] = useState(token);
  const [queue, setQueue] = useState<Transaction[]>([]);
  const [recent, setRecent] = useState<Transaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const { connected, events, revision } = useEventStream(token);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const [held, all] = await Promise.all([
        listTransactions(token, "held"),
        listTransactions(token),
      ]);
      setQueue(held);
      setRecent(all);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "failed to load");
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Every stream event changes the queue or the ledger: re-fetch is the
  // simplest correct merge, and at demo scale it costs nothing.
  useEffect(() => {
    if (revision > 0) void refresh();
  }, [revision, refresh]);

  const decide = async (action: "approve" | "reject", id: string) => {
    setBusyId(id);
    try {
      await (action === "approve" ? approve(token, id) : reject(token, id));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "decision failed");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main>
      <header>
        <h1>SentinelPay</h1>
        <span className={connected ? "badge live" : "badge down"}>
          {connected ? "live" : "offline"}
        </span>
        <form
          className="token-form"
          onSubmit={(formEvent) => {
            formEvent.preventDefault();
            localStorage.setItem(TOKEN_STORAGE_KEY, draftToken);
            setToken(draftToken);
          }}
        >
          <input
            type="password"
            placeholder="analyst token"
            value={draftToken}
            onChange={(change) => setDraftToken(change.target.value)}
          />
          <button type="submit">connect</button>
        </form>
      </header>

      {error && <p className="error" role="alert">{error}</p>}

      {!token && <p className="empty">Enter the analyst token (ANALYST_TOKEN in .env) to connect.</p>}

      <section aria-label="review queue">
        <h2>Review queue {queue.length > 0 && <span className="count">{queue.length}</span>}</h2>
        {queue.length === 0 && <p className="empty">Nothing held. Funds are moving on their own.</p>}
        {queue.map((tx) => (
          <article key={tx.id} className={`card ${decisionClass(tx)}`}>
            <div className="amount">{money(tx.amount_minor, tx.currency)}</div>
            <div className="meta">
              <span className={`pill ${decisionClass(tx)}`}>{tx.status}</span>
              <span>decision {tx.decision}</span>
              <span>score {tx.risk_score?.toFixed(3) ?? "—"}</span>
              <span>{new Date(tx.created_at).toLocaleTimeString()}</span>
            </div>
            <div className="rules">{tx.rules_fired.join(", ") || "no rules fired"}</div>
            <div className="actions">
              <button
                disabled={busyId === tx.id}
                onClick={() => decide("approve", tx.id)}
                className="approve"
              >
                approve
              </button>
              <button
                disabled={busyId === tx.id}
                onClick={() => decide("reject", tx.id)}
                className="reject"
              >
                reject
              </button>
            </div>
          </article>
        ))}
      </section>

      <div className="columns">
        <section aria-label="live feed">
          <h2>Live feed</h2>
          {events.length === 0 && <p className="empty">Waiting for events…</p>}
          <ul className="feed">
            {events.map((event) => (
              <li key={event.id}>
                <span className="type">{event.type}</span>
                <span>{eventSummary(event)}</span>
                <time>{new Date(event.at).toLocaleTimeString()}</time>
              </li>
            ))}
          </ul>
        </section>

        <section aria-label="recent transactions">
          <h2>Recent</h2>
          <table>
            <thead>
              <tr>
                <th>amount</th>
                <th>status</th>
                <th>decision</th>
                <th>score</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((tx) => (
                <tr key={tx.id} className={decisionClass(tx)}>
                  <td>{money(tx.amount_minor, tx.currency)}</td>
                  <td>{tx.status}</td>
                  <td>{tx.decision ?? "—"}</td>
                  <td>{tx.risk_score?.toFixed(3) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </main>
  );
}
