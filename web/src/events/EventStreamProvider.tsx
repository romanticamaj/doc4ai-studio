import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { eventsUrl } from "@/api/client";
import { EVENT_KINDS, type AidocEvent, type EventKind } from "@/api/types";
import { applyEvent } from "./applyEvent";
import { logStore } from "./useTaskLogs";

type Ctx = { connected: boolean; reconnect: () => void; subscribe: (fn: (ev: AidocEvent) => void) => () => void };
const EventStreamContext = createContext<Ctx>({ connected: false, reconnect: () => {}, subscribe: () => () => {} });

export function useEventStream() {
  return useContext(EventStreamContext);
}

/**
 * One EventSource for the whole app. The browser reconnects by itself (sending Last-Event-ID) after a dropped
 * connection; if the stream is closed for good (e.g. 401 before a token is entered) it is reopened after a
 * backoff with `?last_event_id=` so the server can still replay what was missed.
 */
export function EventStreamProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [generation, setGeneration] = useState(0);
  const lastSeq = useRef<number | null>(null);
  const listeners = useRef(new Set<(ev: AidocEvent) => void>());

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let delay = 1000;
    let disposed = false;
    let wasDown = false;

    const open = () => {
      let url = eventsUrl();
      if (lastSeq.current != null) url += `${url.includes("?") ? "&" : "?"}last_event_id=${lastSeq.current}`;
      es = new EventSource(url);
      es.onopen = () => {
        delay = 1000;
        setConnected(true);
        logStore.setStale(false);
        if (wasDown) {
          // the server may have restarted or replayed only part of what happened: refetch what is on screen
          wasDown = false;
          void qc.invalidateQueries({ queryKey: ["jobs"] });
          void qc.invalidateQueries({ queryKey: ["documents"] });
          void qc.invalidateQueries({ queryKey: ["system"] });
        }
      };
      es.onerror = () => {
        wasDown = true;
        setConnected(false);
        logStore.setStale(true);
        if (es && es.readyState === EventSource.CLOSED && !disposed) {
          es.close();
          retry = setTimeout(open, delay);
          delay = Math.min(delay * 2, 15000);
        }
      };
      const handler = (kind: EventKind) => (e: MessageEvent) => {
        let payload: unknown = {};
        try {
          payload = e.data ? JSON.parse(e.data) : {};
        } catch {
          return;
        }
        const seq = e.lastEventId ? Number(e.lastEventId) : 0;
        if (seq) lastSeq.current = seq;
        const ev = { kind, seq, payload } as AidocEvent;
        applyEvent(qc, ev);
        listeners.current.forEach((fn) => fn(ev));
      };
      for (const kind of EVENT_KINDS) es.addEventListener(kind, handler(kind) as EventListener);
    };
    open();
    return () => {
      disposed = true;
      clearTimeout(retry);
      es?.close();
    };
  }, [qc, generation]);

  const reconnect = useCallback(() => setGeneration((g) => g + 1), []);
  const subscribe = useCallback((fn: (ev: AidocEvent) => void) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);

  return <EventStreamContext.Provider value={{ connected, reconnect, subscribe }}>{children}</EventStreamContext.Provider>;
}
