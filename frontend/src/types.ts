export type Decision = "APPROVE" | "REVIEW" | "DECLINE" | null;

export interface Transaction {
  id: string;
  status: "settled" | "held" | "declined";
  from_account_id: string;
  to_account_id: string;
  amount_minor: number;
  currency: string;
  description: string | null;
  created_at: string;
  decision: Decision;
  risk_score: number | null;
  model_version: string | null;
  rules_fired: string[];
  review_status: string | null;
}

export interface FeedEvent {
  id: string;
  at: string;
  type: string;
  transaction?: Transaction;
  transaction_id?: string;
  action?: string;
  analyst?: string;
  status?: string;
  review_status?: string;
}

export function money(amountMinor: number, currency: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(
    amountMinor / 100,
  );
}

export function decisionClass(tx: Transaction): string {
  if (tx.status === "held") return "held";
  if (tx.status === "declined") return "declined";
  return "settled";
}
