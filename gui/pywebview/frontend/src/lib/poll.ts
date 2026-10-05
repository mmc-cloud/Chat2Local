import { useEffect, useState } from "react";
import { errorMessage } from "./bridge";
// Schedule after completion, so slow IPC calls never accumulate.
export function usePoll<T>(
  load: () => Promise<T>,
  delay: number,
  enabled = true,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await load();
        if (active) {
          setData(result);
          setError("");
        }
      } catch (error) {
        if (active) setError(errorMessage(error));
      }
      if (active) timer = setTimeout(poll, delay);
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [load, delay, enabled]);
  return { data, error };
}
