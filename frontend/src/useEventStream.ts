import { useEffect, useRef, useState } from "react";
import type { FeedEvent } from "./types";

export interface StreamState {
  connected: boolean;
  events: FeedEvent[];
  /** Bumped whenever any event arrives; effects can re-fetch on change. */
  revision: number;
}

const MAX_FEED_EVENTS = 50;

/**
 * Subscribes to /api/events with the analyst token and folds the stream into
 * state. EventSource reconnects on its own; we track liveness for the badge.
 */
export function useEventStream(token: string): StreamState {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const revisionRef = useRef(0);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    if (!token) {
      setConnected(false);
      return;
    }
    const source = new EventSource(
      `/api/events?analyst_token=${encodeURIComponent(token)}`,
    );

    const onEvent = (messageEvent: MessageEvent) => {
      try {
        const parsed = JSON.parse(messageEvent.data) as FeedEvent;
        revisionRef.current += 1;
        setRevision(revisionRef.current);
        setEvents((previous) => [parsed, ...previous].slice(0, MAX_FEED_EVENTS));
      } catch {
        // A malformed frame is a bug, but never a reason to kill the feed.
      }
    };

    source.addEventListener("transaction.created", onEvent);
    source.addEventListener("review.decided", onEvent);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    return () => source.close();
  }, [token]);

  return { connected, events, revision };
}
