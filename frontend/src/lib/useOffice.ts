"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { OfficeApi } from "./api";
import type { ActivityEvent, OfficeState } from "./types";

const MAX_EVENTS = 80;

export interface OfficeData {
  state: OfficeState | null;
  events: ActivityEvent[]; // newest last
  online: boolean; // true only while the last poll succeeded
  loaded: boolean; // first successful poll finished
  lastError: string | null;
}

/** Polls the backend. Everything shown in the office comes from here; nothing is simulated in the browser. */
export function useOffice(baseUrl: string, intervalMs = 2000): OfficeData {
  const [state, setState] = useState<OfficeState | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [online, setOnline] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const seq = useRef(0);
  const api = useMemo(() => new OfficeApi(baseUrl, (u, i) => fetch(u, { ...i, cache: "no-store" })), [baseUrl]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ctl = new AbortController();

    const tick = async () => {
      try {
        const s = await api.state(ctl.signal);
        const e = await api.events(seq.current, ctl.signal);
        if (stopped) return;
        if (s.latest_seq < seq.current) seq.current = 0; // backend restarted: sequence numbers reset
        else if (e.events.length) seq.current = e.events[e.events.length - 1].seq;
        setState(s);
        if (e.events.length) setEvents((old) => [...old, ...e.events].slice(-MAX_EVENTS));
        setOnline(true);
        setLoaded(true);
        setLastError(null);
      } catch (err) {
        if (stopped) return;
        setOnline(false);
        setLastError(err instanceof Error ? err.message : "unknown error");
      } finally {
        if (!stopped) timer = setTimeout(tick, intervalMs);
      }
    };
    tick();
    return () => { stopped = true; ctl.abort(); if (timer) clearTimeout(timer); };
  }, [api, intervalMs]);

  return { state, events, online, loaded, lastError };
}
